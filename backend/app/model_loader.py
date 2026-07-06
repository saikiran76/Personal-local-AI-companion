"""Model Loader — manages local LLM loading with quantization and QLoRA.

Supports two loading paths:
  1. llama-cpp-python (GGUF files) — fast CPU/GPU inference
  2. transformers + bitsandbytes INT8 — HuggingFace models with 8-bit quantization
     + optional QLoRA adapters for weight adaptation

Falls back to mock mode if neither backend is available.
"""

import asyncio
import gc
import json
import logging
import os
import re
import subprocess
import time
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------
try:
    from llama_cpp import Llama as LlamaModel
    LLAMA_CPP_AVAILABLE = True
except ImportError:
    LLAMA_CPP_AVAILABLE = False
    logger.info("llama-cpp-python not installed — GGUF path unavailable.")

try:
    import torch
    import bitsandbytes as bnb
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
    )
    from peft import PeftModel, prepare_model_for_kbit_training, get_peft_model, LoraConfig
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False
    logger.info("transformers/bitsandbytes not installed — HF quantized path unavailable.")

# ---------------------------------------------------------------------------
# Device memory detection
# ---------------------------------------------------------------------------
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


def _get_total_vram_mb() -> int:
    """Query NVIDIA GPU VRAM via nvidia-smi (returns 0 if unavailable)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Take first GPU
            return int(result.stdout.strip().split("\n")[0].strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Fallback: torch.cuda
    if TRANSFORMERS_AVAILABLE and torch.cuda.is_available():
        return torch.cuda.get_device_properties(0).total_mem // (1024 * 1024)

    return 0


def _get_total_ram_mb() -> int:
    """Query system RAM."""
    if PSUTIL_AVAILABLE:
        return psutil.virtual_memory().total // (1024 * 1024)
    # Fallback: platform-specific
    try:
        import platform
        if platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "memorychip", "get", "capacity"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines = [l.strip() for l in result.stdout.strip().split("\n")[1:] if l.strip()]
                return sum(int(l) for l in lines) // (1024 * 1024)
        elif platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def detect_compute() -> dict:
    """
    Detect available compute resources.

    Returns dict with:
      device:     "cuda" | "mps" | "cpu"
      vram_mb:    total GPU VRAM in MB (0 if no discrete GPU)
      ram_mb:     total system RAM in MB
      tier:       "high" | "medium" | "low"
      device_name: GPU name string
    """
    device = "cpu"
    device_name = "CPU"
    vram_mb = _get_total_vram_mb()
    ram_mb = _get_total_ram_mb()

    # Detect device type
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            device = "cuda"
            device_name = result.stdout.strip().split("\n")[0].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if device == "cpu" and TRANSFORMERS_AVAILABLE:
        if torch.cuda.is_available():
            device = "cuda"
            device_name = torch.cuda.get_device_name(0)
            vram_mb = torch.cuda.get_device_properties(0).total_mem // (1024 * 1024)
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
            device_name = "Apple Silicon (MPS)"

    # Determine tier based on memory
    #   high:   >=12 GB VRAM or >=32 GB RAM — can run 7B+ models comfortably
    #   medium: >=6 GB VRAM or >=16 GB RAM — can run quantized 3-7B models
    #   low:    <6 GB VRAM and <16 GB RAM  — small models only
    effective_mem = max(vram_mb, ram_mb)
    if effective_mem >= 12000 or vram_mb >= 6000:
        tier = "high"
    elif effective_mem >= 6000:
        tier = "medium"
    else:
        tier = "low"

    return {
        "device": device,
        "device_name": device_name,
        "vram_mb": vram_mb,
        "ram_mb": ram_mb,
        "tier": tier,
    }


# ---------------------------------------------------------------------------
# Model registries
# ---------------------------------------------------------------------------
# GGUF models (llama-cpp-python path) — name -> (repo, filename, context, vram_req_mb)
GGUF_REGISTRY: dict[str, tuple[str, str, int, int]] = {
    "Qwen2.5-7B": (
        "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "qwen2.5-7b-instruct-q4_k_m.gguf",
        32768,
        4500,
    ),
    "Llama-3.1-8B": (
        "QuantFactory/Meta-Llama-3.1-8B-Instruct-GGUF",
        "Meta-Llama-3.1-8B-Instruct.Q4_K_M.gguf",
        131072,
        5000,
    ),
    "Phi-3.5-Mini": (
        "bartowski/Phi-3.5-mini-instruct-GGUF",
        "Phi-3.5-mini-instruct-Q4_K_M.gguf",
        32768,
        2500,
    ),
}

# HuggingFace models (transformers + bitsandbytes INT8 path)
#   name -> (repo_id, context, vram_req_mb_for_int8)
HF_REGISTRY: dict[str, tuple[str, int, int]] = {
    "Qwen2.5-7B-INT8": (
        "Qwen/Qwen2.5-7B-Instruct",
        32768,
        8000,
    ),
    "Llama-3.1-8B-INT8": (
        "meta-llama/Meta-Llama-3.1-8B-Instruct",
        131072,
        8500,
    ),
    "Phi-3.5-Mini-INT8": (
        "microsoft/Phi-3.5-mini-instruct",
        32768,
        4000,
    ),
    "SmolLM2-1.7B-INT8": (
        "HuggingFaceTB/SmolLM2-1.7B-Instruct",
        8192,
        2500,
    ),
}

# Combined registry for user-facing name resolution
ALL_MODELS = {**GGUF_REGISTRY, **HF_REGISTRY}

DEFAULT_MODEL_DIR = Path.home() / ".desktop-companion" / "models"
DEFAULT_ADAPTER_DIR = Path.home() / ".desktop-companion" / "adapters"


class ModelStatus(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    QUANTIZING = "quantizing"
    READY = "ready"
    ERROR = "error"


@dataclass
class QuantizationConfig:
    """Quantization settings for the model."""
    enabled: bool = True
    bits: int = 8  # 4 or 8
    quant_type: str = "nf4"  # "nf4" or "fp4" for QLoRA; "int8" for pure bitsandbytes
    double_quant: bool = True  # nested quantization saves additional ~0.4 GB
    compute_dtype: str = "float16"  # "float16" or "bfloat16"


@dataclass
class LoRAConfig:
    """QLoRA adapter configuration."""
    enabled: bool = False
    r: int = 16  # rank
    lora_alpha: int = 32  # scaling factor
    lora_dropout: float = 0.05
    target_modules: list[str] = field(default_factory=lambda: [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ])
    adapter_path: str | None = None  # path to saved adapter weights


@dataclass
class ModelInfo:
    name: str
    status: ModelStatus = ModelStatus.IDLE
    device: str = "cpu"
    device_name: str = "CPU"
    tier: str = "low"
    load_time_ms: float = 0
    download_progress: float = 0.0
    error: str | None = None
    model_path: str | None = None
    quantization: str | None = None  # "int8", "nf4", "gguf-q4", etc.
    vram_mb: int = 0
    ram_mb: int = 0
    adapter_loaded: bool = False


class ModelLoader:
    """
    Manages local LLM lifecycle with quantization support.

    Loading paths (tried in priority order):
      1. GGUF via llama-cpp-python (fastest, native quantization)
      2. HuggingFace transformers + bitsandbytes INT8/QLoRA
      3. Mock mode (no dependencies required)
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        quant_config: QuantizationConfig | None = None,
        lora_config: LoRAConfig | None = None,
    ):
        self._model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._adapter_dir = DEFAULT_ADAPTER_DIR
        self._adapter_dir.mkdir(parents=True, exist_ok=True)

        self._model = None
        self._tokenizer = None
        self._info = ModelInfo(name="")
        self._lock = asyncio.Lock()
        self._compute: dict | None = None
        self._quant_config = quant_config or QuantizationConfig()
        self._lora_config = lora_config or LoRAConfig()

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def is_ready(self) -> bool:
        return self._info.status == ModelStatus.READY

    @property
    def compute(self) -> dict:
        if self._compute is None:
            self._compute = detect_compute()
        return self._compute

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------
    async def load(self, model_name: str, model_path: str | None = None) -> ModelInfo:
        """Load a model — auto-selects path based on available compute."""
        async with self._lock:
            if self._info.status == ModelStatus.LOADING:
                return self._info

            self._info = ModelInfo(name=model_name, status=ModelStatus.LOADING)
            logger.info("Loading model: %s", model_name)

            start = time.monotonic()

            try:
                comp = self.compute
                self._info.device = comp["device"]
                self._info.device_name = comp["device_name"]
                self._info.tier = comp["tier"]
                self._info.vram_mb = comp["vram_mb"]
                self._info.ram_mb = comp["ram_mb"]
                logger.info(
                    "Compute: %s (%s) — VRAM: %d MB, RAM: %d MB, tier: %s",
                    comp["device_name"], comp["device"],
                    comp["vram_mb"], comp["ram_mb"], comp["tier"],
                )

                # Auto-select best model for this machine
                model_name = self._select_model_for_compute(model_name, comp)
                self._info.name = model_name
                logger.info("Selected model: %s", model_name)

                # Pick loading path
                if model_name in GGUF_REGISTRY and (LLAMA_CPP_AVAILABLE or model_path):
                    await self._load_gguf(model_name, model_path, comp)
                elif model_name in HF_REGISTRY and TRANSFORMERS_AVAILABLE:
                    await self._load_hf_int8(model_name, comp)
                elif LLAMA_CPP_AVAILABLE:
                    # Fallback: try any GGUF registry entry
                    fallback = self._pick_gguf_fallback(comp)
                    if fallback:
                        logger.info("Falling back to GGUF model: %s", fallback)
                        model_name = fallback
                        self._info.name = model_name
                        await self._load_gguf(model_name, None, comp)
                    else:
                        await self._load_mock(model_name)
                else:
                    await self._load_mock(model_name)

                self._info.status = ModelStatus.READY
                self._info.load_time_ms = (time.monotonic() - start) * 1000
                logger.info(
                    "Model loaded in %.0fms — %s on %s (quant: %s)",
                    self._info.load_time_ms, model_name,
                    comp["device"], self._info.quantization or "none",
                )

            except Exception as e:
                self._info.status = ModelStatus.ERROR
                self._info.error = str(e)
                logger.error("Model load failed: %s", e, exc_info=True)

            return self._info

    def unload(self):
        """Unload model from memory and free VRAM."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None
        gc.collect()
        if TRANSFORMERS_AVAILABLE and torch.cuda.is_available():
            torch.cuda.empty_cache()
        self._info = ModelInfo(name="")
        logger.info("Model unloaded and VRAM cleared")

    async def generate(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream token generation from the loaded model."""
        if not self.is_ready:
            yield json.dumps({"error": "Model not ready"})
            return

        if LLAMA_CPP_AVAILABLE and self._model is not None and hasattr(self._model, "create_chat_completion"):
            async for token in self._generate_llamacpp(messages, max_tokens, temperature, stop):
                yield token
        elif TRANSFORMERS_AVAILABLE and self._model is not None and hasattr(self._model, "generate"):
            async for token in self._generate_hf(messages, max_tokens, temperature):
                yield token
        else:
            async for token in self._generate_mock():
                yield token

    # -------------------------------------------------------------------
    # GGUF loading path (llama-cpp-python)
    # -------------------------------------------------------------------
    async def _load_gguf(self, model_name: str, model_path: str | None, comp: dict):
        """Load a GGUF model via llama-cpp-python."""
        resolved_path = await self._resolve_gguf(model_name, model_path)
        self._info.model_path = str(resolved_path)

        _, _, ctx_size, vram_req = GGUF_REGISTRY.get(model_name, ("", "", 4096, 3000))

        n_gpu_layers = 0
        if comp["device"] == "cuda":
            n_gpu_layers = -1  # offload all layers to GPU
        elif comp["device"] == "mps":
            n_gpu_layers = 1

        # Reduce context if VRAM is tight
        if comp["vram_mb"] > 0 and comp["vram_mb"] < vram_req:
            ctx_size = min(ctx_size, 4096)
            logger.warning("Limited VRAM — reducing context to %d", ctx_size)

        def _load():
            return LlamaModel(
                model_path=str(resolved_path),
                n_ctx=ctx_size,
                n_gpu_layers=n_gpu_layers,
                n_threads=os.cpu_count() or 4,
                verbose=False,
            )

        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(None, _load)
        self._info.quantization = "gguf-q4"

    async def _resolve_gguf(self, model_name: str, model_path: str | None = None) -> Path:
        """Resolve GGUF model file path, downloading if needed."""
        if model_path:
            p = Path(model_path)
            if p.exists():
                return p
            raise FileNotFoundError(f"Model file not found: {model_path}")

        if model_name not in GGUF_REGISTRY:
            raise ValueError(
                f"Unknown GGUF model: {model_name}. "
                f"Available: {list(GGUF_REGISTRY.keys())}"
            )

        repo, filename, _, _ = GGUF_REGISTRY[model_name]
        cache_path = self._model_dir / filename

        if cache_path.exists():
            logger.info("Using cached GGUF: %s", cache_path)
            return cache_path

        logger.info("Downloading %s from %s...", filename, repo)
        self._info.status = ModelStatus.DOWNLOADING

        from huggingface_hub import hf_hub_download

        hf_token = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_HUB_TOKEN")
        loop = asyncio.get_event_loop()
        downloaded = await loop.run_in_executor(
            None,
            lambda: hf_hub_download(
                repo_id=repo,
                filename=filename,
                local_dir=str(self._model_dir),
                local_dir_use_symlinks=False,
                token=hf_token,
            ),
        )
        return Path(downloaded)

    def _pick_gguf_fallback(self, comp: dict) -> str | None:
        """Pick a GGUF model that fits within available resources."""
        candidates = sorted(
            GGUF_REGISTRY.items(),
            key=lambda x: x[1][3],  # sort by vram_req ascending
        )
        for name, (_, _, _, vram_req) in candidates:
            if comp["vram_mb"] == 0 or comp["vram_mb"] >= vram_req:
                return name
        return "Phi-3.5-Mini"  # smallest option

    # -------------------------------------------------------------------
    # HuggingFace + bitsandbytes INT8 loading path
    # -------------------------------------------------------------------
    async def _load_hf_int8(self, model_name: str, comp: dict):
        """
        Load a HuggingFace model with bitsandbytes INT8 quantization.

        Flow:
          1. Build BitsAndBytesConfig for INT8 (or NF4 for QLoRA)
          2. Load tokenizer
          3. Load model with quantization_config
          4. If QLoRA enabled: attach LoRA adapter via peft
        """
        repo_id, ctx_size, vram_req = HF_REGISTRY[model_name]

        # Determine quantization mode
        if self._lora_config.enabled:
            # QLoRA: NF4 quantization + LoRA adapter
            quant_bits = 4
            quant_type = self._lora_config.quant_type
            self._info.quantization = f"qlora-{quant_type}"
        else:
            # Pure INT8 quantization
            quant_bits = self._quant_config.bits
            quant_type = "int8"
            self._info.quantization = "int8"

        logger.info(
            "Loading %s with %d-bit quantization (type=%s, double_quant=%s)",
            repo_id, quant_bits, quant_type, self._quant_config.double_quant,
        )

        compute_dtype = getattr(torch, self._quant_config.compute_dtype, torch.float16)

        if quant_bits == 4:
            bnb_config = BitsAndBytesConfig(
                load_in_8bit=False,
                load_in_4bit=True,
                bnb_4bit_quant_type=quant_type,
                bnb_4bit_use_double_quant=self._quant_config.double_quant,
                bnb_4bit_compute_dtype=compute_dtype,
            )
        else:
            bnb_config = BitsAndBytesConfig(
                load_in_8bit=True,
                load_in_4bit=False,
            )

        loop = asyncio.get_event_loop()

        # Load tokenizer
        def _load_tokenizer():
            return AutoTokenizer.from_pretrained(
                repo_id,
                trust_remote_code=True,
            )

        self._tokenizer = await loop.run_in_executor(None, _load_tokenizer)

        # Load model with quantization
        self._info.status = ModelStatus.QUANTIZING

        def _load_model():
            model = AutoModelForCausalLM.from_pretrained(
                repo_id,
                quantization_config=bnb_config,
                device_map="auto",
                trust_remote_code=True,
                dtype=compute_dtype,
            )
            if self._lora_config.enabled:
                model = prepare_model_for_kbit_training(model)
            return model

        self._model = await loop.run_in_executor(None, _load_model)

        # Attach QLoRA adapter if enabled
        if self._lora_config.enabled:
            await self._attach_lora()

        # Set padding token if missing
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

    async def _attach_lora(self):
        """Attach a QLoRA adapter to the loaded model."""
        adapter_path = self._lora_config.adapter_path

        if adapter_path and Path(adapter_path).exists():
            # Load existing adapter
            logger.info("Loading existing LoRA adapter from %s", adapter_path)
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: PeftModel.from_pretrained(self._model, adapter_path),
            )
            self._info.adapter_loaded = True
            logger.info("LoRA adapter loaded successfully")
        else:
            # Initialize fresh LoRA weights on the model
            logger.info(
                "Initializing fresh LoRA adapter (r=%d, alpha=%d, dropout=%.2f)",
                self._lora_config.r,
                self._lora_config.lora_alpha,
                self._lora_config.lora_dropout,
            )
            peft_config = LoraConfig(
                r=self._lora_config.r,
                lora_alpha=self._lora_config.lora_alpha,
                lora_dropout=self._lora_config.lora_dropout,
                target_modules=self._lora_config.target_modules,
                bias="none",
                task_type="CAUSAL_LM",
            )
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: get_peft_model(self._model, peft_config),
            )
            self._info.adapter_loaded = True

            # Print trainable parameters info
            trainable, total = self._model.get_nb_trainable_parameters()
            logger.info(
                "QLoRA adapter attached — trainable: %d / %d (%.2f%%)",
                trainable, total, 100 * trainable / total,
            )

    async def _generate_hf(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[str]:
        """Stream tokens from a HuggingFace transformers model with INT8/QLoRA."""
        loop = asyncio.get_event_loop()

        # Build prompt from messages
        prompt = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )

        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)

        def _generate():
            streamer = None
            # Use TextIteratorStreamer for async streaming
            from transformers import TextIteratorStreamer

            streamer = TextIteratorStreamer(
                self._tokenizer, skip_prompt=True, skip_special_tokens=True,
            )

            gen_kwargs = {
                **inputs,
                "max_new_tokens": max_tokens,
                "temperature": max(temperature, 0.01),
                "do_sample": temperature > 0,
                "streamer": streamer,
            }

            import threading
            thread = threading.Thread(target=self._model.generate, kwargs=gen_kwargs)
            thread.start()

            return streamer, thread

        streamer, thread = await loop.run_in_executor(None, _generate)

        # Stream tokens from the TextIteratorStreamer
        for text_chunk in streamer:
            if text_chunk:
                yield text_chunk

        thread.join(timeout=30)

    # -------------------------------------------------------------------
    # Mock loading / generation (no dependencies)
    # -------------------------------------------------------------------
    async def _load_mock(self, model_name: str):
        """Simulate model loading when no inference backend is available."""
        logger.info("Mock mode: simulating load for '%s'", model_name)
        self._info.quantization = "mock"
        await asyncio.sleep(1.5)

    async def _generate_mock(self) -> AsyncIterator[str]:
        """Simulate streaming token generation."""
        mock_tokens = [
            "I've processed your request locally. ",
            "Since everything runs on your device, ",
            "your data never left your machine. ",
            "Here's what I found:\n\n",
            "**Analysis complete.** ",
            "The task has been handled by the local model. ",
            "No cloud API was involved in this response.",
        ]
        for token in mock_tokens:
            await asyncio.sleep(0.08 + (time.monotonic() % 0.05))
            yield token
        yield json.dumps({"finish_reason": "stop"})

    # -------------------------------------------------------------------
    # Compute-aware model selection
    # -------------------------------------------------------------------
    def _select_model_for_compute(self, model_name: str, comp: dict) -> str:
        """
        Choose the best model for the detected hardware.

        Priority:
          1. User-specified model (if it exists in any registry)
          2. "auto" / "default" → pick by tier
          3. Unknown name → fallback to smallest available
        """
        tier = comp["tier"]
        vram = comp["vram_mb"]

        # If user explicitly requested a model that exists, try to use it
        if model_name in GGUF_REGISTRY:
            # Check if VRAM is sufficient
            _, _, _, vram_req = GGUF_REGISTRY[model_name]
            if vram > 0 and vram < vram_req * 0.7:
                logger.warning(
                    "Requested %s needs ~%d MB VRAM but only %d MB available — downgrading",
                    model_name, vram_req, vram,
                )
                return self._pick_gguf_fallback(comp) or "Phi-3.5-Mini"
            return model_name

        if model_name in HF_REGISTRY:
            _, _, vram_req = HF_REGISTRY[model_name]
            if vram > 0 and vram < vram_req * 0.7:
                logger.warning(
                    "Requested %s needs ~%d MB VRAM but only %d MB available — downgrading",
                    model_name, vram_req, vram,
                )
                if TRANSFORMERS_AVAILABLE:
                    return "SmolLM2-1.7B-INT8"
                return self._pick_gguf_fallback(comp) or "Phi-3.5-Mini"
            return model_name

        # "auto" selection based on tier
        if model_name.lower() in {"auto", "default", "best", "glm-5.2", "llm"}:
            if tier == "high":
                if TRANSFORMERS_AVAILABLE:
                    return "Qwen2.5-7B-INT8"
                return "Qwen2.5-7B"
            elif tier == "medium":
                if TRANSFORMERS_AVAILABLE:
                    return "Phi-3.5-Mini-INT8"
                return "Phi-3.5-Mini"
            else:
                if TRANSFORMERS_AVAILABLE:
                    return "SmolLM2-1.7B-INT8"
                return "Phi-3.5-Mini"

        # Unknown name — try to find closest match or fallback
        logger.warning("Unknown model '%s' — selecting best fit for tier=%s", model_name, tier)
        if tier == "high":
            return "Qwen2.5-7B" if LLAMA_CPP_AVAILABLE else "Qwen2.5-7B-INT8"
        elif tier == "medium":
            return "Phi-3.5-Mini"
        else:
            return "SmolLM2-1.7B-INT8" if TRANSFORMERS_AVAILABLE else "Phi-3.5-Mini"
