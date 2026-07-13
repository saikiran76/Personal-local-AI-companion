"""Voice Manager — STT client, TTS manager, model downloads.

Handles:
- Spawning voice_stt.exe (faster-whisper) as subprocess
- Downloading piper.exe + voice model for TTS
- Sentence chunking for TTS audio generation
- WAV file management in ~/.desktop-companion/audio/
"""

import asyncio
import base64
import json
import logging
import struct
import subprocess
import sys
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

VOICE_DIR = Path.home() / ".desktop-companion" / "voice"
AUDIO_DIR = Path.home() / ".desktop-companion" / "audio"
STT_MODEL_DIR = VOICE_DIR / "stt_models"
PIPER_DIR = VOICE_DIR / "piper"
PIPER_EXE = PIPER_DIR / "piper.exe"
PIPER_MODEL = PIPER_DIR / "en_US-lessac-medium.onnx"
PIPER_MODEL_JSON = PIPER_DIR / "en_US-lessac-medium.onnx.json"

# Piper download URLs (GitHub releases)
PIPER_EXE_URL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_win64.zip"
PIPER_MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
PIPER_MODEL_JSON_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"

# Sentence boundary pattern for TTS chunking
_SENTENCE_END = frozenset(".!?")


class STTClient:
    """Client for voice_stt.exe subprocess (faster-whisper STT).

    Communicates via stdio JSON-RPC, same pattern as DirectStdioTransport.
    """

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._start_lock = asyncio.Lock()
        self._ready = False

    async def start(self):
        """Spawn voice_stt.exe subprocess."""
        async with self._start_lock:
            if self.is_ready:
                return
            if self._process is not None and self._process.poll() is None:
                return

            await self._start_process()

    async def _start_process(self):
        """Spawn voice_stt.exe subprocess once start() has acquired the lock."""
        if getattr(sys, 'frozen', False):
            # Frozen mode — look for voice_stt.exe next to backend.exe or in voice_stt/ subfolder
            backend_dir = Path(sys.executable).parent
            stt_exe = backend_dir / "voice_stt.exe"
            if not stt_exe.exists():
                # Try onedir layout: voice_stt/voice_stt.exe
                stt_exe = backend_dir / "voice_stt" / "voice_stt.exe"
            if not stt_exe.exists():
                logger.warning("voice_stt.exe not found at %s or %s",
                               backend_dir / "voice_stt.exe", backend_dir / "voice_stt" / "voice_stt.exe")
                return
            command = str(stt_exe)
            args = []
        else:
            # Dev mode — use venv Python
            backend_dir = Path(__file__).resolve().parent.parent
            python = str(backend_dir / ".venv" / "Scripts" / "python.exe")
            command = python
            args = ["-m", "voice_stt.server"]

        try:
            self._process = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(backend_dir),
            )
            logger.info("Started STT server (PID: %d)", self._process.pid)

            # Forward stderr to logger
            if self._process.stderr:
                t = threading.Thread(target=self._forward_stderr, args=(self._process.stderr,), daemon=True)
                t.start()

            # Send initialize request
            resp = await self._send_request("initialize")
            if "result" in resp:
                self._ready = True
                logger.info("STT server ready: %s", resp["result"].get("serverInfo", {}).get("name"))
            else:
                logger.warning("STT server initialize failed: %s", resp)

        except Exception as e:
            logger.error("Failed to start STT server: %s", e)

    async def stop(self):
        """Stop the STT server subprocess."""
        if self._process:
            try:
                self._process.stdin.close()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None
            self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready and self._process is not None and self._process.poll() is None

    async def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio bytes to text.

        Args:
            wav_bytes: WAV audio (PCM16, mono or stereo, any sample rate)

        Returns:
            Transcribed text string
        """
        if not self.is_ready:
            raise RuntimeError("STT server not ready")

        wav_b64 = base64.b64encode(wav_bytes).decode("ascii")
        resp = await self._send_request("transcribe", {"wav_base64": wav_b64})

        if "error" in resp:
            raise RuntimeError(f"Transcription error: {resp['error'].get('message', 'unknown')}")
        return resp.get("result", {}).get("text", "")

    async def check_model_status(self) -> dict:
        """Check if the STT model loaded successfully."""
        if not self.is_ready:
            return {"model_loaded": False, "error": "STT server not ready"}
        try:
            resp = await self._send_request("model_status")
            return resp.get("result", {"model_loaded": False})
        except Exception as e:
            return {"model_loaded": False, "error": str(e)}

    async def _send_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        request = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params:
            request["params"] = params

        request_bytes = (json.dumps(request) + "\n").encode("utf-8")
        loop = asyncio.get_event_loop()

        try:
            await loop.run_in_executor(None, self._write, request_bytes)
            response = await loop.run_in_executor(None, self._read_response)
            return response
        except Exception as e:
            logger.error("STT communication error: %s", e)
            return {"error": str(e)}

    def _write(self, data: bytes):
        if self._process and self._process.stdin:
            try:
                self._process.stdin.write(data)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def _read_response(self) -> dict:
        if not self._process or not self._process.stdout:
            return {"error": "No stdout pipe"}
        try:
            line = self._process.stdout.readline()
            if not line:
                return {"error": "STT server closed stdout"}
            return json.loads(line.decode("utf-8").strip())
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON from STT server: {e}"}

    def _forward_stderr(self, pipe):
        for line in pipe:
            try:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.info("[voice_stt] %s", text)
            except Exception:
                pass


class TTSManager:
    """Text-to-Speech manager using piper.exe.

    Downloads piper.exe and voice model on first use.
    Generates WAV audio files for text sentences.
    """

    def __init__(self):
        self._available = False
        self._initializing = False

    async def ensure_ready(self) -> bool:
        """Ensure piper.exe and model are downloaded and ready."""
        if self._available:
            return True
        if self._initializing:
            return False

        self._initializing = True
        try:
            return await self._download_piper()
        finally:
            self._initializing = False

    async def _download_piper(self) -> bool:
        """Download piper.exe and voice model if not present."""
        PIPER_DIR.mkdir(parents=True, exist_ok=True)

        # Check if already present
        if PIPER_EXE.exists() and PIPER_MODEL.exists():
            self._available = True
            logger.info("Piper TTS ready: %s", PIPER_EXE)
            return True

        try:
            import httpx

            # Download piper.exe
            if not PIPER_EXE.exists():
                logger.info("Downloading piper.exe...")
                # piper releases have a zip; we need to extract piper.exe
                zip_path = PIPER_DIR / "piper.zip"
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.get(PIPER_EXE_URL, follow_redirects=True)
                    resp.raise_for_status()
                    zip_path.write_bytes(resp.content)

                # Extract piper.exe from zip
                import zipfile
                with zipfile.ZipFile(zip_path, "r") as zf:
                    for name in zf.namelist():
                        if name.endswith("piper.exe"):
                            # Extract to PIPER_DIR/piper.exe
                            data = zf.read(name)
                            PIPER_EXE.write_bytes(data)
                            break
                    else:
                        logger.error("piper.exe not found in zip")
                        return False
                zip_path.unlink()
                logger.info("piper.exe downloaded")

            # Download voice model
            if not PIPER_MODEL.exists():
                logger.info("Downloading voice model...")
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.get(PIPER_MODEL_URL, follow_redirects=True)
                    resp.raise_for_status()
                    PIPER_MODEL.write_bytes(resp.content)

                    resp = await client.get(PIPER_MODEL_JSON_URL, follow_redirects=True)
                    resp.raise_for_status()
                    PIPER_MODEL_JSON.write_bytes(resp.content)
                logger.info("Voice model downloaded")

            self._available = True
            return True

        except Exception as e:
            logger.error("Failed to download Piper TTS: %s", e)
            return False

    async def synthesize(self, text: str) -> str | None:
        """Synthesize text to WAV audio file.

        Args:
            text: Text to speak

        Returns:
            Path to generated WAV file, or None on failure
        """
        if not self._available:
            if not await self.ensure_ready():
                return None

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        output_path = AUDIO_DIR / f"tts_{uuid.uuid4().hex[:8]}.wav"

        try:
            # piper.exe reads text from stdin, writes WAV to stdout
            process = await asyncio.create_subprocess_exec(
                str(PIPER_EXE),
                "--model", str(PIPER_MODEL),
                "--output_file", str(output_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate(input=text.encode("utf-8"))

            if process.returncode != 0:
                logger.error("Piper TTS failed: %s", stderr.decode("utf-8", errors="replace"))
                return None

            if output_path.exists() and output_path.stat().st_size > 0:
                return str(output_path)
            else:
                logger.error("Piper produced no output")
                return None

        except Exception as e:
            logger.error("Piper TTS error: %s", e)
            return None


def chunk_sentences(text: str) -> list[str]:
    """Split text into sentences for TTS chunking.

    Yields sentence strings as they complete (for streaming).
    A sentence ends at ., !, or ? followed by space or end of string.
    """
    current = []
    for char in text:
        current.append(char)
        if char in _SENTENCE_END:
            sentence = "".join(current).strip()
            if sentence:
                yield sentence
            current = []

    # Flush remaining text
    remainder = "".join(current).strip()
    if remainder:
        yield remainder


# Singleton instances (initialized in server.py startup)
stt_client = STTClient()
tts_manager = TTSManager()
