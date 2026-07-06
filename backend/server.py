"""Desktop Companion — Python Backend Entry Point."""

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.config import load_config
from app.model_loader import ModelLoader
from app.agent import AgentOrchestrator
from app.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("backend")

app = FastAPI(title="Desktop Companion Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# Global state
model_loader = ModelLoader()
agent: AgentOrchestrator | None = None


@app.on_event("startup")
async def startup():
    """On startup: load config, signal Electron we're alive."""
    config = load_config()
    logger.info("Config loaded: %s", config)
    # Emit startup event so Electron knows backend is ready
    # Model loading happens on first /chat request (lazy)


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down backend...")
    model_loader.unload()


def main():
    """Run the FastAPI server on a fixed port."""
    port = int(os.environ.get("BACKEND_PORT", 8765))
    logger.info("Starting backend on port %d", port)
    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
