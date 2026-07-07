"""Model Loader — GGUF-only inference via llama-cpp-python.

Philosophy:
  - GPU available → CUDA acceleration (n_gpu_layers = -1)
  - No GPU       → CPU-optimized GGUF execution (n_threads = cores)
  - GGUF models are pre-quantized: Q4_K_M, Q5_K_M, Q8_0, etc.
  - Compute-aware selection picks the right model + quant for available RAM/VRAM

Falls back to mock mode if llama-cpp-python is not installed.
"""

import asyncio
import gc
import json
import logging
import os
import subprocess
import time
from enum import Enum
from dataclasses import dataclass
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
    logger.info(
        "llama-cpp-python not installed — using mock mode. "
        "Install with: uv pip install llama-cpp-python"
    )

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------
def _get_total_vram_mb() -> int:
    """Query NVIDIA GPU VRAM via nvidia-smi (returns 0 if unavailable)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split("\n")[0].strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return 0


def _get_total_ram_mb() -> int:
    """Query system RAM."""
    if PSUTIL_AVAILABLE:
        return psutil.virtual_memory().total // (1024 * 1024)
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


def _detect_nvidia_gpu() -> str:
    """Return GPU name string via nvidia-smi, or empty string."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def detect_compute() -> dict:
    """
    Detect available compute resources.

    Returns:
      device:      "cuda" | "mps" | "cpu"
      device_name: human-readable GPU name (or "CPU")
      vram_mb:     GPU VRAM in MB (0 if no discrete GPU)
      ram_mb:      system RAM in MB
      cpu_cores:   logical CPU cores
      tier:        "high" | "medium" | "low"
    """
    cpu_cores = os.cpu_count() or 4
    ram_mb = _get_total_ram_mb()
    vram_mb = 0
    device = "cpu"
    device_name = "CPU"

    # --- GPU detection ---
    nvidia_name = _detect_nvidia_gpu()
    if nvidia_name:
        device = "cuda"
        device_name = nvidia_name
        vram_mb = _get_total_vram_mb()
    else:
        # Check MPS (Apple Silicon)
        try:
            import torch
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
                device_name = "Apple Silicon (MPS)"
        except ImportError:
            pass

    # --- Tier classification ---
    # Use the larger of VRAM and RAM to decide model size.
    # On CPU-only machines, RAM is the bottleneck; on GPU machines, VRAM is.
    effective = max(vram_mb, ram_mb)
    if effective >= 12000 or vram_mb >= 6000:
        tier = "high"
    elif effective >= 6000:
        tier = "medium"
    else:
        tier = "low"

    return {
        "device": device,
        "device_name": device_name,
        "vram_mb": vram_mb,
        "ram_mb": ram_mb,
        "cpu_cores": cpu_cores,
        "tier": tier,
    }


# ---------------------------------------------------------------------------
# GGUF Model Registry
# ---------------------------------------------------------------------------
# Each entry: (repo_id, filename, context_size, ram_required_mb)
#
# ram_required_mb is a rough estimate of what's needed to load the model
# on CPU (for GPU, VRAM is the constraint — handled separately in _load_gguf).

GGUF_REGISTRY: dict[str, tuple[str, str, int, int]] = {
    # --- Qwen 2.5 family ---
    "Qwen2.5-7B-Q4_K_M": (
        "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "qwen2.5-7b-instruct-Q4_K_M.gguf",
        32768,
        4500,
    ),
    "Qwen2.5-7B-Q5_K_M": (
        "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "qwen2.5-7b-instruct-Q5_K_M.gguf",
        32768,
        5200,
    ),
    "Qwen2.5-7B-Q8_0": (
        "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "qwen2.5-7b-instruct-Q8_0.gguf",
        32768,
        7500,
    ),

    # --- Llama 3.1 family ---
    "Llama-3.1-8B-Q4_K_M": (
        "QuantFactory/Meta-Llama-3.1-8B-Instruct-GGUF",
        "Meta-Llama-3.1-8B-Instruct.Q4_K_M.gguf",
        131072,
        5000,
    ),
    "Llama-3.1-8B-Q5_K_M": (
        "QuantFactory/Meta-Llama-3.1-8B-Instruct-GGUF",
        "Meta-Llama-3.1-8B-Instruct.Q5_K_M.gguf",
        131072,
        5800,
    ),

    # --- Phi-3.5 Mini (small, fast) ---
    "Phi-3.5-Mini-Q4_K_M": (
        "bartowski/Phi-3.5-mini-instruct-GGUF",
        "Phi-3.5-mini-instruct-Q4_K_M.gguf",
        32768,
        2500,
    ),

    # --- SmolLM2 (ultra-light) ---
    "SmolLM2-1.7B-Q4_K_M": (
        "HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF",
        "SmolLM2-1.7B-Instruct-Q4_K_M.gguf",
        8192,
        1200,
    ),
}

# Friendly aliases — map simple names to the best GGUF variant
_MODEL_ALIASES: dict[str, str] = {
    # User-facing names → registry keys
    "qwen2.5-7b": "Qwen2.5-7B-Q4_K_M",
    "qwen 2.5 7b": "Qwen2.5-7B-Q4_K_M",
    "llama-3.1-8b": "Llama-3.1-8B-Q4_K_M",
    "llama 3.1 8b": "Llama-3.1-8B-Q4_K_M",
    "phi-3.5-mini": "Phi-3.5-Mini-Q4_K_M",
    "phi 3.5 mini": "Phi-3.5-Mini-Q4_K_M",
    "smollm2": "SmolLM2-1.7B-Q4_K_M",
    "glm-5.2": "Qwen2.5-7B-Q4_K_M",  # alias for the default model
    "glm5.2": "Qwen2.5-7B-Q4_K_M",
    "default": "Qwen2.5-7B-Q4_K_M",
    "auto": "Qwen2.5-7B-Q4_K_M",
    "best": "Qwen2.5-7B-Q4_K_M",
}

DEFAULT_MODEL_DIR = Path.home() / ".desktop-companion" / "models"


class ModelStatus(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass
class ModelInfo:
    name: str
    status: ModelStatus = ModelStatus.IDLE
    device: str = "cpu"
    device_name: str = "CPU"
    tier: str = "low"
    cpu_cores: int = 4
    load_time_ms: float = 0
    error: str | None = None
    model_path: str | None = None
    quantization: str | None = None  # e.g. "Q4_K_M", "Q8_0", "mock"
    vram_mb: int = 0
    ram_mb: int = 0
    n_gpu_layers: int = 0
    n_threads: int = 4
    is_mock: bool = False  # True when running without a real model file


class ModelLoader:
    """
    GGUF-only model manager via llama-cpp-python.

    Compute strategy:
      GPU (CUDA) → n_gpu_layers = -1 (offload all layers to VRAM)
      GPU (MPS)  → n_gpu_layers = 1 (Metal acceleration)
      CPU        → n_threads = cpu_cores, n_gpu_layers = 0

    Model selection strategy:
      tier=high   → 7B Q5_K_M or Q8_0 (quality)
      tier=medium → 7B Q4_K_M or 3B Q4_K_M (balanced)
      tier=low    → 1.7B Q4_K_M (speed)
    """

    def __init__(self, model_dir: str | Path | None = None):
        self._model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._info = ModelInfo(name="")
        self._lock = asyncio.Lock()
        self._compute: dict | None = None

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
        """Load a GGUF model — auto-selects variant based on available hardware."""
        async with self._lock:
            if self._info.status == ModelStatus.LOADING:
                return self._info

            self._info = ModelInfo(name=model_name, status=ModelStatus.LOADING)
            start = time.monotonic()

            try:
                comp = self.compute
                self._info.device = comp["device"]
                self._info.device_name = comp["device_name"]
                self._info.tier = comp["tier"]
                self._info.vram_mb = comp["vram_mb"]
                self._info.ram_mb = comp["ram_mb"]
                self._info.cpu_cores = comp["cpu_cores"]
                logger.info(
                    "Hardware: %s | VRAM: %d MB | RAM: %d MB | Cores: %d | Tier: %s",
                    comp["device_name"], comp["vram_mb"],
                    comp["ram_mb"], comp["cpu_cores"], comp["tier"],
                )

                # Resolve model name (handle aliases)
                resolved = self._resolve_model_name(model_name)
                self._info.name = resolved

                # Check if llama-cpp-python is available
                if not LLAMA_CPP_AVAILABLE and not model_path:
                    await self._load_mock(resolved)
                else:
                    await self._load_gguf(resolved, model_path, comp)

                self._info.status = ModelStatus.READY
                self._info.load_time_ms = (time.monotonic() - start) * 1000
                logger.info(
                    "Ready: %s | %s | %s | %d layers on GPU, %d threads CPU | %.0fms",
                    resolved, comp["device"], self._info.quantization or "?",
                    self._info.n_gpu_layers, self._info.n_threads,
                    self._info.load_time_ms,
                )

            except Exception as e:
                self._info.status = ModelStatus.ERROR
                self._info.error = str(e)
                self._info.is_mock = True
                self._info.quantization = "mock"
                logger.error("Load failed: %s", e, exc_info=True)

            return self._info

    def unload(self):
        """Unload model and free memory."""
        if self._model is not None:
            del self._model
            self._model = None
        gc.collect()
        self._info = ModelInfo(name="")
        logger.info("Model unloaded")

    async def generate(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from the loaded GGUF model."""
        if not self.is_ready:
            yield json.dumps({"error": "Model not ready"})
            return

        if LLAMA_CPP_AVAILABLE and self._model is not None:
            async for token in self._generate_stream(messages, max_tokens, temperature, stop):
                yield token
        else:
            async for token in self._generate_mock():
                yield token

    # -------------------------------------------------------------------
    # GGUF loading
    # -------------------------------------------------------------------
    async def _load_gguf(self, model_name: str, model_path: str | None, comp: dict):
        """
        Load a GGUF model with hardware-appropriate settings.

        GPU path: offload all layers to VRAM for maximum speed.
        CPU path: use all available threads for best throughput.
        """
        resolved_path = await self._resolve_path(model_name, model_path)
        self._info.model_path = str(resolved_path)

        _, _, ctx_size, ram_req = GGUF_REGISTRY.get(model_name, ("", "", 4096, 3000))

        # --- GPU allocation ---
        device = comp["device"]
        vram_mb = comp["vram_mb"]

        if device == "cuda" and vram_mb > 0:
            # CUDA: offload as many layers as VRAM allows
            # Q4_K_M ~550 bytes/param; Q8_0 ~1 byte/param
            # Rough heuristic: each GPU layer needs ~50-80 MB for a 7B model
            if vram_mb >= ram_req:
                n_gpu_layers = -1  # offload everything
            elif vram_mb >= ram_req * 0.6:
                n_gpu_layers = 20  # partial offload
            else:
                n_gpu_layers = 8   # minimal offload
            logger.info("CUDA: offloading %d layers to GPU (%d MB VRAM)", n_gpu_layers, vram_mb)
        elif device == "mps":
            n_gpu_layers = 1
            logger.info("MPS: using Metal acceleration")
        else:
            n_gpu_layers = 0
            logger.info("CPU-only: all layers on CPU")

        self._info.n_gpu_layers = n_gpu_layers

        # --- CPU thread count ---
        n_threads = min(comp["cpu_cores"], 8)  # cap at 8 to avoid thread contention
        self._info.n_threads = n_threads

        # --- Context size: reduce if RAM is tight ---
        if comp["ram_mb"] > 0 and comp["ram_mb"] < ram_req * 1.2:
            ctx_size = min(ctx_size, 4096)
            logger.info("Tight RAM — context reduced to %d", ctx_size)

        # --- Quantization label from filename ---
        fname = resolved_path.name.lower()
        if "q8_0" in fname:
            self._info.quantization = "Q8_0"
        elif "q6_k" in fname:
            self._info.quantization = "Q6_K"
        elif "q5_k_m" in fname:
            self._info.quantization = "Q5_K_M"
        elif "q5_k_s" in fname:
            self._info.quantization = "Q5_K_S"
        elif "q4_k_m" in fname:
            self._info.quantization = "Q4_K_M"
        elif "q4_k_s" in fname:
            self._info.quantization = "Q4_K_S"
        elif "q3_k" in fname:
            self._info.quantization = "Q3_K"
        elif "q2_k" in fname:
            self._info.quantization = "Q2_K"
        elif "iq" in fname:
            self._info.quantization = "IQ"
        else:
            self._info.quantization = "GGUF"

        # --- Load via llama-cpp-python ---
        def _load():
            return LlamaModel(
                model_path=str(resolved_path),
                n_ctx=ctx_size,
                n_gpu_layers=n_gpu_layers,
                n_threads=n_threads,
                verbose=False,
            )

        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(None, _load)

    async def _resolve_path(self, model_name: str, model_path: str | None = None) -> Path:
        """Resolve GGUF file path — find local file first, download if needed."""
        if model_path:
            p = Path(model_path)
            if p.exists():
                return p
            raise FileNotFoundError(f"Model file not found: {model_path}")

        # If model_name looks like a file path, use it directly
        if model_name.endswith(".gguf"):
            p = self._model_dir / model_name
            if p.exists():
                return p
            # Also check without directory prefix
            p = Path(model_name)
            if p.exists():
                return p

        # Check if there's a matching file in the models directory (case-insensitive)
        if model_name in GGUF_REGISTRY:
            _, filename, _, _ = GGUF_REGISTRY[model_name]
            cache_path = self._model_dir / filename
            if cache_path.exists():
                logger.info("Cached: %s", cache_path)
                return cache_path

            # Try case-insensitive match in models dir
            for fpath in self._model_dir.glob("*.gguf"):
                if fpath.name.lower() == filename.lower():
                    logger.info("Found (case-insensitive): %s", fpath)
                    return fpath

        # Scan models directory for any .gguf files (imported models)
        local_models = sorted(
            self._model_dir.glob("*.gguf"),
            key=lambda f: f.stat().st_size,
            reverse=True,  # prefer largest
        )
        if local_models:
            logger.info("Using local model: %s", local_models[0])
            return local_models[0]

        # Last resort: try to download from registry
        if model_name in GGUF_REGISTRY:
            repo, filename, _, _ = GGUF_REGISTRY[model_name]
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

        raise FileNotFoundError(
            f"No model files found. Import a .gguf model first."
        )

    def _resolve_model_name(self, name: str) -> str:
        """Resolve user input to a GGUF_REGISTRY key or local file name."""
        # Direct match in registry
        if name in GGUF_REGISTRY:
            return name

        # Check if there are local .gguf files first — always prefer local
        local_models = sorted(
            self._model_dir.glob("*.gguf"),
            key=lambda f: f.stat().st_size,
            reverse=True,
        )
        if local_models:
            logger.info("Found local model: %s", local_models[0].name)
            return local_models[0].stem

        # Alias match (only if no local files)
        key = name.lower().strip().replace("_", "-").replace(" ", " ")
        if key in _MODEL_ALIASES:
            return _MODEL_ALIASES[key]

        # Partial match
        for alias, target in _MODEL_ALIASES.items():
            if key in alias or alias in key:
                return target

        # Unknown — pick by tier from registry
        logger.warning("Unknown model '%s' — auto-selecting for tier=%s", name, self.compute["tier"])
        return self._pick_by_tier(self.compute["tier"])

    def _pick_by_tier(self, tier: str) -> str:
        """Pick the best model variant for a compute tier."""
        if tier == "high":
            # Prefer quality: Q8_0 or Q5_K_M
            for name in ["Qwen2.5-7B-Q8_0", "Qwen2.5-7B-Q5_K_M", "Llama-3.1-8B-Q5_K_M"]:
                if name in GGUF_REGISTRY:
                    return name
        elif tier == "medium":
            # Balanced: Q4_K_M
            for name in ["Qwen2.5-7B-Q4_K_M", "Phi-3.5-Mini-Q4_K_M"]:
                if name in GGUF_REGISTRY:
                    return name
        else:
            # Speed: smallest model
            for name in ["SmolLM2-1.7B-Q4_K_M", "Phi-3.5-Mini-Q4_K_M"]:
                if name in GGUF_REGISTRY:
                    return name
        return "Phi-3.5-Mini-Q4_K_M"

    # -------------------------------------------------------------------
    # Streaming inference
    # -------------------------------------------------------------------
    async def _generate_stream(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        stop: list[str] | None,
    ) -> AsyncIterator[str]:
        """Stream tokens from llama-cpp-python without blocking the event loop."""
        import queue

        token_queue: queue.Queue = queue.Queue()

        def _generate_in_thread():
            """Run in executor thread — yields tokens into a queue."""
            try:
                stream = self._model.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=max(temperature, 0.01),
                    stop=stop or [],
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    finish_reason = chunk.get("choices", [{}])[0].get("finish_reason")

                    if token:
                        token_queue.put(("token", token))

                    if finish_reason:
                        token_queue.put(("done", finish_reason))
                        break

            except Exception as e:
                logger.error("Generation error: %s", e)
                token_queue.put(("error", str(e)))

            finally:
                token_queue.put(("stop", None))

        loop = asyncio.get_event_loop()
        # Run the generator in a thread so it doesn't block the event loop
        loop.run_in_executor(None, _generate_in_thread)

        # Read tokens from the queue (non-blocking to the executor)
        while True:
            msg_type, data = await loop.run_in_executor(None, token_queue.get)

            if msg_type == "token":
                yield data
            elif msg_type == "done":
                yield json.dumps({"finish_reason": data})
                break
            elif msg_type == "error":
                yield json.dumps({"error": data})
                break
            elif msg_type == "stop":
                break

    # -------------------------------------------------------------------
    # Mock (no llama-cpp-python)
    # -------------------------------------------------------------------
    async def _load_mock(self, model_name: str):
        logger.info("Mock mode: simulating load for '%s'", model_name)
        self._info.quantization = "mock"
        self._info.is_mock = True
        await asyncio.sleep(1.5)

    async def _generate_mock(self) -> AsyncIterator[str]:
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
