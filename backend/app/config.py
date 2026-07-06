"""Application configuration — reads from local JSON store."""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field


# Default config path — mirrors electron-store location
DEFAULT_CONFIG_DIR = Path.home() / ".desktop-companion"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"


@dataclass
class QuantizationConfig:
    """bitsandbytes quantization settings."""
    enabled: bool = True
    bits: int = 8  # 4 for QLoRA, 8 for pure INT8
    quant_type: str = "int8"  # "int8" | "nf4" | "fp4"
    double_quant: bool = True
    compute_dtype: str = "float16"


@dataclass
class LoRAConfig:
    """QLoRA adapter configuration."""
    enabled: bool = False
    r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: list[str] = field(default_factory=lambda: [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ])
    adapter_path: str | None = None


@dataclass
class AppConfig:
    ai_preference: str = "local"  # local | hybrid | cloud
    model: str = "auto"  # model name or "auto" for compute-aware selection
    model_path: str | None = None  # path to GGUF/ONNX if bring-your-own
    data_location: str = "default"
    user_name: str = "User"
    assistant_name: str = "Companion"
    language: str = "en"
    theme: str = "light"
    # Quantization
    quantization: QuantizationConfig = field(default_factory=QuantizationConfig)
    lora: LoRAConfig = field(default_factory=LoRAConfig)
    # MCP servers
    mcp_filesystem: bool = True
    mcp_notes: bool = True
    mcp_browser: bool = True


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load config from local JSON file. Falls back to defaults."""
    config_path = Path(path) if path else DEFAULT_CONFIG_FILE

    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                data = json.load(f)

            # Extract nested quantization config
            quant_data = data.pop("quantization", None)
            lora_data = data.pop("lora", None)

            # Filter to known fields
            known = {k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__}
            config = AppConfig(**known)

            if quant_data and isinstance(quant_data, dict):
                config.quantization = QuantizationConfig(**{
                    k: v for k, v in quant_data.items()
                    if k in QuantizationConfig.__dataclass_fields__
                })
            if lora_data and isinstance(lora_data, dict):
                config.lora = LoRAConfig(**{
                    k: v for k, v in lora_data.items()
                    if k in LoRAConfig.__dataclass_fields__
                })

            return config
        except Exception as e:
            print(f"Warning: Could not load config from {config_path}: {e}")

    return AppConfig()


def save_config(config: AppConfig, path: str | Path | None = None):
    """Save config to local JSON file."""
    config_path = Path(path) if path else DEFAULT_CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "ai_preference": config.ai_preference,
        "model": config.model,
        "model_path": config.model_path,
        "data_location": config.data_location,
        "user_name": config.user_name,
        "assistant_name": config.assistant_name,
        "language": config.language,
        "theme": config.theme,
        "quantization": {
            "enabled": config.quantization.enabled,
            "bits": config.quantization.bits,
            "quant_type": config.quantization.quant_type,
            "double_quant": config.quantization.double_quant,
            "compute_dtype": config.quantization.compute_dtype,
        },
        "lora": {
            "enabled": config.lora.enabled,
            "r": config.lora.r,
            "lora_alpha": config.lora.lora_alpha,
            "lora_dropout": config.lora.lora_dropout,
            "target_modules": config.lora.target_modules,
            "adapter_path": config.lora.adapter_path,
        },
        "mcp_filesystem": config.mcp_filesystem,
        "mcp_notes": config.mcp_notes,
        "mcp_browser": config.mcp_browser,
    }

    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)
