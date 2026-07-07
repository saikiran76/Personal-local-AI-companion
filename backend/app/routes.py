"""FastAPI Routes - SSE streaming endpoint for chat."""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File
from sse_starlette.sse import EventSourceResponse

from .model_loader import (
    ModelLoader, ModelStatus, GGUF_REGISTRY, DEFAULT_MODEL_DIR,
    detect_compute, advise_model_upgrade, list_upgrade_options,
    get_model_tool_capability, get_model_family,
)
from .agent import AgentOrchestrator
from .mcp_client import MCPClientManager
from .config import load_config, save_config

logger = logging.getLogger(__name__)
router = APIRouter()

_model_loader: ModelLoader | None = None
_mcp_manager = MCPClientManager()
_agent: AgentOrchestrator | None = None
_pending_model_path: str | None = None  # set by import to trigger reload on next connect


@router.get("/health")
async def health():
    if _model_loader is None:
        return {"status": "ok", "model": None}
    return {"status": "ok", "model": _model_loader.info.__dict__}


@router.get("/status")
async def status():
    """Return current backend status."""
    if _model_loader is None:
        return {
            "model_status": "idle",
            "model_name": "",
            "model_device": "cpu",
            "model_load_time_ms": 0,
            "model_path": None,
            "quantization": None,
            "mcp_servers_connected": _mcp_manager._connected,
            "tools_available": list(_mcp_manager._tools.keys()),
        }
    info = _model_loader.info
    return {
        "model_status": info.status.value,
        "model_name": info.name,
        "model_device": info.device,
        "model_device_name": info.device_name,
        "model_tier": info.tier,
        "model_load_time_ms": info.load_time_ms,
        "model_path": info.model_path,
        "quantization": info.quantization,
        "vram_mb": info.vram_mb,
        "ram_mb": info.ram_mb,
        "n_gpu_layers": info.n_gpu_layers,
        "n_threads": info.n_threads,
        "cpu_cores": info.cpu_cores,
        "model_available": not info.is_mock,
        "mcp_servers_connected": _mcp_manager._connected,
        "tools_available": list(_mcp_manager._tools.keys()),
    }


@router.get("/events")
async def events(request: Request):
    """
    SSE endpoint - streams status events to Electron on connection.

    Events:
      connected, model_loading, model_ready, model_error, backend_ready

    Detects pending model switch from import endpoint and reloads.
    """
    global _model_loader, _agent, _pending_model_path

    config = load_config()

    async def event_generator():
        global _model_loader, _agent, _pending_model_path

        yield {
            "event": "connected",
            "data": json.dumps({"message": "Backend connected"}),
        }

        # --- Check if a model switch was requested by the import endpoint ---
        needs_reload = False
        if _pending_model_path is not None:
            logger.info("Pending model switch detected: %s", _pending_model_path)
            # Unload the old model
            if _model_loader is not None:
                _model_loader.unload()
                logger.info("Unloaded previous model")
            _model_loader = None
            _agent = None  # agent references old model, must recreate
            needs_reload = True

        # --- Create loader if needed ---
        if _model_loader is None:
            _model_loader = ModelLoader()

        # --- Load model if not ready or if a switch was requested ---
        if not _model_loader.is_ready or needs_reload:
            # Determine what model to load
            model_name = config.model
            model_path = _pending_model_path  # use pending path if available

            if config.ai_preference == "local":
                yield {
                    "event": "model_loading",
                    "data": json.dumps({
                        "model": model_name,
                        "message": f"Loading {model_name}...",
                    }),
                }

                model_info = await _model_loader.load(model_name, model_path)
            else:
                model_info = _model_loader.info
                model_info.status = ModelStatus.READY
                model_info.is_mock = True
                model_info.quantization = "mock"

            # Clear pending switch after load attempt
            _pending_model_path = None
        else:
            model_info = _model_loader.info
            logger.info("Reusing existing model: %s", model_info.name)

        if model_info.status == ModelStatus.READY:
            yield {
                "event": "model_ready",
                "data": json.dumps({
                    "model": model_info.name,
                    "device": model_info.device,
                    "device_name": model_info.device_name,
                    "tier": model_info.tier,
                    "load_time_ms": round(model_info.load_time_ms),
                    "model_path": model_info.model_path,
                    "quantization": model_info.quantization,
                    "vram_mb": model_info.vram_mb,
                    "ram_mb": model_info.ram_mb,
                    "n_gpu_layers": model_info.n_gpu_layers,
                    "n_threads": model_info.n_threads,
                    "model_available": not model_info.is_mock,
                }),
            }

            if model_info.is_mock:
                yield {
                    "event": "model_missing",
                    "data": json.dumps({
                        "message": "No model file found. Import a .gguf model to enable local AI.",
                        "model": model_info.name,
                    }),
                }
        elif model_info.status == ModelStatus.ERROR:
            logger.warning("Model load failed, falling back to mock mode: %s", model_info.error)
            model_info.status = ModelStatus.READY
            model_info.is_mock = True
            model_info.quantization = "mock"

            yield {
                "event": "model_ready",
                "data": json.dumps({
                    "model": model_info.name,
                    "device": model_info.device,
                    "device_name": model_info.device_name,
                    "tier": model_info.tier,
                    "load_time_ms": 0,
                    "model_path": None,
                    "quantization": "mock",
                    "vram_mb": model_info.vram_mb,
                    "ram_mb": model_info.ram_mb,
                    "n_gpu_layers": 0,
                    "n_threads": model_info.n_threads,
                    "model_available": False,
                    "load_error": model_info.error,
                }),
            }
            yield {
                "event": "model_missing",
                "data": json.dumps({
                    "message": f"Model could not be loaded: {model_info.error}. Import a .gguf model to enable local AI.",
                    "model": model_info.name,
                }),
            }
        else:
            yield {
                "event": "model_error",
                "data": json.dumps({"error": f"Unexpected status: {model_info.status}"}),
            }

        # Initialize agent if not already done
        if _agent is None:
            _agent = AgentOrchestrator(_model_loader, _mcp_manager)
            await _agent.initialize()

        yield {
            "event": "backend_ready",
            "data": json.dumps({
                "tools": list(_mcp_manager._tools.keys()),
                "model": _model_loader.info.name,
                "quantization": _model_loader.info.quantization,
                "model_available": not _model_loader.info.is_mock,
            }),
        }

        try:
            while True:
                await asyncio.sleep(15)
                yield {"event": "ping", "data": "{}"}
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(event_generator())


@router.post("/chat")
async def chat(request: Request):
    """
    Streaming chat endpoint. Streams AgentEvents via SSE.

    Request:  { "message": "user prompt" }
    Events:   token, clear, thinking, tool_call, tool_result, done, error
    """
    body = await request.json()
    user_message = body.get("message", "")

    if not user_message:
        return {"error": "No message provided"}

    if _agent is None:
        return {"error": "Agent not initialized. Wait for backend_ready event."}

    async def stream_response():
        try:
            async for event in _agent.chat(user_message):
                if event.type == "token":
                    yield {
                        "event": "token",
                        "data": json.dumps({"content": event.content}),
                    }
                elif event.type == "thinking":
                    yield {
                        "event": "thinking",
                        "data": json.dumps({"content": event.content}),
                    }
                elif event.type == "clear":
                    yield {"event": "clear", "data": json.dumps({})}
                elif event.type == "tool_call":
                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "tool": event.tool_name,
                            "arguments": event.tool_args,
                            "message": event.content,
                        }),
                    }
                elif event.type == "tool_result":
                    yield {
                        "event": "tool_result",
                        "data": json.dumps({
                            "tool": event.tool_name,
                            "result": event.tool_result,
                        }),
                    }
                    # Send immediate ping to keep connection alive during next round
                    await asyncio.sleep(0.01)  # flush event
                    yield {"event": "ping", "data": "{}"}
                elif event.type == "done":
                    yield {"event": "done", "data": json.dumps({})}
                elif event.type == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": event.content}),
                    }
        except Exception as e:
            logger.error("Chat error: %s", e, exc_info=True)
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(stream_response())


@router.post("/reset")
async def reset():
    if _agent:
        _agent.reset()
    return {"status": "reset"}


@router.get("/models/list")
async def list_models():
    """
    List all available GGUF models in the models directory.
    Returns both registry-known models and any imported .gguf files.
    """
    models_dir = Path(DEFAULT_MODEL_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)

    available = []

    # Check GGUF_REGISTRY for files that exist locally
    for name, (repo, filename, ctx, ram_req) in GGUF_REGISTRY.items():
        fpath = models_dir / filename
        if fpath.exists():
            size_mb = fpath.stat().st_size // (1024 * 1024)
            available.append({
                "name": name,
                "filename": filename,
                "path": str(fpath),
                "size_mb": size_mb,
                "source": "registry",
                "context": ctx,
                "ram_required_mb": ram_req,
            })

    # Scan for any .gguf files not in registry (user-imported)
    registry_files = {entry[1] for entry in GGUF_REGISTRY.values()}
    for fpath in models_dir.glob("*.gguf"):
        if fpath.name not in registry_files:
            size_mb = fpath.stat().st_size // (1024 * 1024)
            available.append({
                "name": fpath.stem,
                "filename": fpath.name,
                "path": str(fpath),
                "size_mb": size_mb,
                "source": "imported",
                "context": 4096,
                "ram_required_mb": size_mb,
            })

    return {"models": available, "directory": str(models_dir)}


@router.post("/models/import")
async def import_model(file: UploadFile = File(...)):
    """
    Import a .gguf model file by uploading it.
    Copies the file to the models directory and triggers a model reload on next connect.
    """
    global _model_loader, _agent, _pending_model_path

    if not file.filename or not file.filename.endswith(".gguf"):
        return {"error": "Only .gguf files are supported", "success": False}

    models_dir = Path(DEFAULT_MODEL_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)
    dest_path = models_dir / file.filename

    # Skip if already exists
    if dest_path.exists():
        size_mb = dest_path.stat().st_size // (1024 * 1024)
        # Even if the file exists, if it's a different model than what's loaded, trigger reload
        if _model_loader and _model_loader.is_ready:
            loaded_path = Path(_model_loader.info.model_path) if _model_loader.info.model_path else None
            if loaded_path and loaded_path.resolve() != dest_path.resolve():
                logger.info("Same filename exists but different model is loaded — triggering reload")
                _pending_model_path = str(dest_path)
                _model_loader.unload()
                _agent = None
        return {
            "success": True,
            "skipped": True,
            "reason": "Model already imported",
            "model": {
                "name": dest_path.stem,
                "filename": file.filename,
                "path": str(dest_path),
                "size_mb": size_mb,
            },
        }

    # Stream upload to disk
    try:
        with open(dest_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                f.write(chunk)

        size_mb = dest_path.stat().st_size // (1024 * 1024)
        logger.info("Imported model: %s (%d MB)", file.filename, size_mb)

        # --- Trigger model switch on next /events reconnect ---
        # Unload the current model so the loader is no longer "ready"
        if _model_loader is not None and _model_loader.is_ready:
            old_name = _model_loader.info.name
            _model_loader.unload()
            _agent = None  # agent references old model, must recreate
            logger.info("Unloaded old model '%s' to make way for new import", old_name)

        # Set the pending path so /events knows to load this specific file
        _pending_model_path = str(dest_path)

        # Update config so the model name persists across restarts
        try:
            config = load_config()
            config.model = dest_path.stem
            save_config(config)
            logger.info("Updated config model to: %s", dest_path.stem)
        except Exception as e:
            logger.warning("Failed to update config: %s", e)

        return {
            "success": True,
            "skipped": False,
            "model_loaded": False,  # will be loaded on next /events reconnect
            "model": {
                "name": dest_path.stem,
                "filename": file.filename,
                "path": str(dest_path),
                "size_mb": size_mb,
            },
        }
    except Exception as e:
        # Clean up partial file
        if dest_path.exists():
            dest_path.unlink()
        logger.error("Model import failed: %s", e)
        return {"error": str(e), "success": False}


@router.post("/models/switch")
async def switch_model(request: Request):
    """
    Switch to a different model by name or file path.
    Unloads current model and loads the new one on next /events reconnect.
    """
    global _model_loader, _agent, _pending_model_path

    body = await request.json()
    model_name = body.get("model", "")
    model_path = body.get("path")

    if not model_name and not model_path:
        return {"error": "Provide 'model' name or 'path' to a .gguf file"}

    # Find the file path if only a name was given
    if model_path:
        dest_path = Path(model_path)
    else:
        models_dir = Path(DEFAULT_MODEL_DIR)
        # Check registry first
        if model_name in GGUF_REGISTRY:
            _, filename, _, _ = GGUF_REGISTRY[model_name]
            dest_path = models_dir / filename
        else:
            # Try as-is, then with .gguf extension
            dest_path = models_dir / model_name
            if not dest_path.exists():
                dest_path = models_dir / f"{model_name}.gguf"
            if not dest_path.exists():
                # Scan for partial match
                for f in models_dir.glob("*.gguf"):
                    if model_name.lower() in f.stem.lower():
                        dest_path = f
                        break

    if not dest_path.exists():
        return {"error": f"Model file not found: {dest_path}", "success": False}

    # Unload current model
    if _model_loader is not None and _model_loader.is_ready:
        old_name = _model_loader.info.name
        _model_loader.unload()
        _agent = None
        logger.info("Unloaded model '%s' for switch to '%s'", old_name, dest_path.stem)

    # Set pending path and update config
    _pending_model_path = str(dest_path)

    try:
        config = load_config()
        config.model = dest_path.stem
        save_config(config)
    except Exception as e:
        logger.warning("Failed to update config: %s", e)

    return {
        "success": True,
        "model": {
            "name": dest_path.stem,
            "path": str(dest_path),
        },
        "message": f"Model switch to '{dest_path.stem}' queued. Reconnect to load.",
    }


@router.get("/models/advise")
async def advise_model():
    """
    Check if the current model should be upgraded for better tool calling.
    Returns upgrade recommendation based on hardware and current model.
    """
    if _model_loader is None:
        return {"upgrade": None, "compute": detect_compute()}

    compute = _model_loader.compute
    current = _model_loader.info.name
    upgrade = advise_model_upgrade(current, compute)
    return {
        "current_model": current,
        "tool_capability": get_model_tool_capability(current),
        "family": get_model_family(current),
        "upgrade": upgrade,
        "compute": compute,
    }


@router.get("/models/upgrade-options")
async def upgrade_options():
    """List all models that could run on this hardware, sorted by capability."""
    compute = detect_compute() if _model_loader is None else _model_loader.compute
    options = list_upgrade_options(compute)
    return {"options": options, "compute": compute}


@router.post("/shutdown")
async def shutdown():
    await _mcp_manager.disconnect_all()
    if _model_loader:
        _model_loader.unload()
    return {"status": "shutting_down"}
