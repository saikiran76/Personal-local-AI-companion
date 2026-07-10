"""
Voice STT Server — Speech-to-Text using faster-whisper.

Standalone PyInstaller binary spawned by the main backend via stdio JSON-RPC.
Provides a single 'transcribe' method that accepts WAV audio and returns text.

Protocol (same as MCP servers):
  Request:  {"jsonrpc": "2.0", "id": N, "method": "transcribe", "params": {"wav_base64": "..."}}
  Response: {"jsonrpc": "2.0", "id": N, "result": {"text": "..."}}
"""

import base64
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("voice_stt")

# Model cache directory
VOICE_DIR = Path.home() / ".desktop-companion" / "voice"
MODEL_DIR = VOICE_DIR / "stt_models"

# Global model reference (lazy loaded)
_model = None
_model_name = "tiny.en"


def _ensure_model():
    """Load the faster-whisper model, downloading if needed."""
    global _model
    if _model is not None:
        return _model

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from faster_whisper import WhisperModel

        logger.info("Loading STT model: %s", _model_name)
        _model = WhisperModel(
            _model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(MODEL_DIR),
        )
        logger.info("STT model loaded successfully")
        return _model
    except Exception as e:
        logger.error("Failed to load STT model: %s", e)
        raise


def handle_request(request: dict) -> dict:
    """Handle a JSON-RPC request from the main backend."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "voice_stt", "version": "0.1.0"},
            },
        }

    elif method == "transcribe":
        try:
            wav_b64 = params.get("wav_base64", "")
            if not wav_b64:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Missing wav_base64"}}

            wav_bytes = base64.b64decode(wav_b64)

            # Write to temp file — faster_whisper reads WAV natively via av
            # Avoids manual WAV parsing + numpy conversion issues
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name

            try:
                model = _ensure_model()
                segments, info = model.transcribe(
                    tmp_path,
                    language="en",
                    beam_size=1,
                    vad_filter=True,
                )
                text = "".join(seg.text for seg in segments).strip()
                logger.info("Transcribed: '%s' (lang=%.2f, dur=%.1fs)", text, info.language_probability, info.duration)
                return {"jsonrpc": "2.0", "id": req_id, "result": {"text": text}}
            finally:
                import os
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except Exception as e:
            logger.error("Transcription failed: %s", e)
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"status": "ok"}}

    elif method == "model_status":
        model_loaded = _model is not None
        return {"jsonrpc": "2.0", "id": req_id, "result": {"model_loaded": model_loaded, "model": _model_name}}

    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def main():
    """Run the STT server, reading JSON-RPC requests from stdin."""
    logger.info("Voice STT server starting (PID: %d)", os.getpid())

    # Pre-load model in background
    try:
        _ensure_model()
    except Exception as e:
        logger.warning("Model pre-load failed (will retry on first request): %s", e)

    # Read requests from stdin (one JSON per line)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
            response = handle_request(request)
        except json.JSONDecodeError as e:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}}

        # Write response to stdout
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
