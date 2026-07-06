"""Root entrypoint for the backend ASGI app.

This lets `uvicorn server:app` work from the project root even though the
actual FastAPI app lives in the backend/ directory.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
BACKEND_SERVER = BACKEND_DIR / "server.py"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

spec = spec_from_file_location("backend_server", BACKEND_SERVER)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load backend server module from {BACKEND_SERVER}")

backend_module = module_from_spec(spec)
sys.modules[spec.name] = backend_module
spec.loader.exec_module(backend_module)

app = backend_module.app

__all__ = ["app"]
