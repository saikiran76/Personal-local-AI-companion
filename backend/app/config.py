"""Application configuration - reads from local JSON store."""

import json
import os
from pathlib import Path
from dataclasses import dataclass


DEFAULT_CONFIG_DIR = Path.home() / ".desktop-companion"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    ai_preference: str = "local"
    model: str = "auto"
    model_path: str | None = None
    data_location: str = "default"
    user_name: str = "User"
    assistant_name: str = "Companion"
    language: str = "en"
    theme: str = "light"
    mcp_filesystem: bool = True
    mcp_notes: bool = True
    mcp_browser: bool = True


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_FILE

    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
            known = {k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__}
            return AppConfig(**known)
        except Exception as e:
            print(f"Warning: Could not load config from {config_path}: {e}")

    return AppConfig()


def save_config(config: AppConfig, path: str | Path | None = None):
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
        "mcp_filesystem": config.mcp_filesystem,
        "mcp_notes": config.mcp_notes,
        "mcp_browser": config.mcp_browser,
    }

    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)
