"""FastAPI Routes — SSE streaming endpoint for chat."""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from .model_loader import ModelLoader, ModelStatus, QuantizationConfig, LoRAConfig
from .agent import AgentOrchestrator
from .mcp_client import MCPClientManager
from .config import load_config

logger = logging.getLogger(__name__)
router = APIRouter()

# Shared instances — config is loaded lazily in events() to pick up quantization settings
_model_loader: ModelLoader | None = None
_mcp_manager = MCPClientManager()
_agent: AgentOrchestrator | None = None


@router.get("/health")
async def health():
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
    return {
        "model_status": _model_loader.info.status.value,
        "model_name": _model_loader.info.name,
        "model_device": _model_loader.info.device,
        "model_device_name": _model_loader.info.device_name,
        "model_tier": _model_loader.info.tier,
        "model_load_time_ms": _model_loader.info.load_time_ms,
        "model_path": _model_loader.info.model_path,
        "quantization": _model_loader.info.quantization,
        "vram_mb": _model_loader.info.vram_mb,
        "ram_mb": _model_loader.info.ram_mb,
        "adapter_loaded": _model_loader.info.adapter_loaded,
        "mcp_servers_connected": _mcp_manager._connected,
        "tools_available": list(_mcp_manager._tools.keys()),
    }


@router.get("/events")
async def events(request: Request):
    """
    SSE endpoint — streams status events to Electron on connection.

    Events emitted:
      - connected: Backend is reachable
      - model_loading: Model download/load started
      - model_ready: Model loaded, inference available
      - model_error: Model load failed
      - backend_ready: Full stack ready (model + MCP tools)
    """
    config = load_config()

    async def event_generator():
        global _model_loader

        # Initial connection
        yield {
            "event": "connected",
            "data": json.dumps({"message": "Backend connected"}),
        }

        # Build ModelLoader with quantization/LoRA config from AppConfig
        quant_cfg = QuantizationConfig(
            enabled=config.quantization.enabled,
            bits=config.quantization.bits,
            quant_type=config.quantization.quant_type,
            double_quant=config.quantization.double_quant,
            compute_dtype=config.quantization.compute_dtype,
        )
        lora_cfg = LoRAConfig(
            enabled=config.lora.enabled,
            r=config.lora.r,
            lora_alpha=config.lora.lora_alpha,
            lora_dropout=config.lora.lora_dropout,
            target_modules=config.lora.target_modules,
            adapter_path=config.lora.adapter_path,
        )
        _model_loader = ModelLoader(quant_config=quant_cfg, lora_config=lora_cfg)

        # Load model if local preference
        if config.ai_preference == "local":
            yield {
                "event": "model_loading",
                "data": json.dumps({
                    "model": config.model,
                    "quantization": config.quantization.quant_type if config.quantization.enabled else "none",
                    "qlora": config.lora.enabled,
                    "message": f"Loading {config.model}...",
                }),
            }

            model_info = await _model_loader.load(config.model, config.model_path)

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
                        "adapter_loaded": model_info.adapter_loaded,
                    }),
                }
            elif model_info.status == ModelStatus.ERROR:
                yield {
                    "event": "model_error",
                    "data": json.dumps({
                        "error": model_info.error or "Unknown error",
                    }),
                }
                return
            else:
                yield {
                    "event": "model_error",
                    "data": json.dumps({
                        "error": f"Unexpected status: {model_info.status}",
                    }),
                }
                return

        # Initialize agent with MCP tools
        global _agent
        _agent = AgentOrchestrator(_model_loader, _mcp_manager)
        await _agent.initialize()

        yield {
            "event": "backend_ready",
            "data": json.dumps({
                "tools": list(_mcp_manager._tools.keys()),
                "model": _model_loader.info.name,
                "quantization": _model_loader.info.quantization,
            }),
        }

        # Keep SSE connection alive
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

    Request body:
      { "message": "user prompt" }

    SSE events:
      - token:        {"content": "..."}             — streaming text chunk
      - tool_call:    {"tool": "name", "arguments": {}} — tool being invoked
      - tool_result:  {"tool": "name", "result": "..."} — tool execution result
      - done:         {}                              — stream complete
      - error:        {"error": "..."}                — error occurred
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
                elif event.type == "done":
                    yield {
                        "event": "done",
                        "data": json.dumps({}),
                    }
                elif event.type == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": event.content}),
                    }

        except Exception as e:
            logger.error("Chat error: %s", e, exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}),
            }

    return EventSourceResponse(stream_response())


@router.post("/reset")
async def reset():
    """Reset conversation history."""
    if _agent:
        _agent.reset()
    return {"status": "reset"}


@router.post("/shutdown")
async def shutdown():
    """Gracefully shutdown backend."""
    await _mcp_manager.disconnect_all()
    _model_loader.unload()
    return {"status": "shutting_down"}
