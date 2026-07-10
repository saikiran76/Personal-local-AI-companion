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
from app.database import db

# --- Monkey-patch for Python 3.13 Windows bug ---
# _ProactorReadPipeTransport._force_close() references self._empty_waiter
# which doesn't exist on that class. Patch it so a dead pipe doesn't crash
# the entire server.
if sys.platform == "win32":
    try:
        import asyncio.proactor_events as _pe

        _orig_force_close = _pe._ProactorReadPipeTransport._force_close

        def _patched_force_close(self, exc=None):
            if not hasattr(self, "_empty_waiter"):
                self._empty_waiter = None
            _orig_force_close(self, exc)

        _pe._ProactorReadPipeTransport._force_close = _patched_force_close
        logging.getLogger("backend").info(
            "Applied asyncio ProactorEventLoop monkey-patch for _empty_waiter bug"
        )
    except Exception as e:
        logging.getLogger("backend").warning(
            "Failed to apply ProactorEventLoop patch: %s", e
        )
# --- End monkey-patch ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("backend")

app = FastAPI(title="Luna Backend", version="0.1.0")

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
    """On startup: init database, load config, signal Electron we're alive."""
    db.init()
    config = load_config()
    logger.info("Config loaded: %s", config)


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down backend...")
    model_loader.unload()
    db.close()


def main():
    """Run the FastAPI server on a fixed port, or serve as an MCP server subprocess."""
    # --- MCP server sub-command mode ---
    if "--mcp-server" in sys.argv:
        idx = sys.argv.index("--mcp-server")
        server_name = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        if server_name:
            _run_mcp_server(server_name)
            return

    """Run the FastAPI server on a fixed port."""
    port = int(os.environ.get("BACKEND_PORT", 8765))
    logger.info("Starting backend on port %d", port)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )


# --- MCP server sub-command registry ---
# Maps server names to their module paths for import.
_MCP_SERVERS = {
    "filesystem": "mcp_servers.filesystem.server",
    "notes": "mcp_servers.notes.server",
    "browser": "mcp_servers.browser.server",
    "email": "mcp_servers.email.server",
    "reminders": "mcp_servers.reminders.server",
}


def _run_mcp_server(name: str):
    """Import and run an MCP server module by name."""
    module_path = _MCP_SERVERS.get(name)
    if not module_path:
        print(f"Unknown MCP server: {name}", file=sys.stderr)
        sys.exit(1)

    import importlib
    try:
        mod = importlib.import_module(module_path)
        mod.main()
    except Exception as e:
        print(f"Failed to start MCP server {name}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
