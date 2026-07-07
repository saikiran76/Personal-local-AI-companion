# Desktop Companion

A local-first AI desktop assistant built with **Electron**, **FastAPI**, **LangChain**, **llama.cpp**, and the **Model Context Protocol (MCP)**. The application runs local GGUF language models, streams responses in real time using Server-Sent Events (SSE), and extends the model with MCP-powered tools for interacting with the filesystem, notes, and the web.

---

# Features

- 🖥️ Cross-platform desktop application powered by Electron
- 🤖 Local GGUF language model inference via `llama.cpp`
- ⚡ Real-time token streaming using Server-Sent Events (SSE)
- 🧠 LangChain-based agent orchestration
- 🔌 Model Context Protocol (MCP) integration
- 📂 Filesystem tools
- 📝 Notes management
- 🌐 Browser and web search tools
- ⚙️ Shared configuration between Electron and Python backend
- 🚀 Automatic GPU acceleration (CUDA / Metal) with CPU fallback
- 📦 Multiple GGUF model variants with automatic hardware-aware selection

---

# Architecture

```text
Electron (Host)
└── ChatScreen
    ── SSE ──► FastAPI Backend (MCP Client)
                ├── /events
                │     └── Status stream
                │         • model_loading
                │         • model_ready
                │
                ├── /chat
                │     └── Token streaming via SSE
                │
                ├── /status
                │     └── Current backend state
                │
                └── AgentOrchestrator
                      ├── ModelLoader
                      │     └── GGUF Model (CPU / CUDA / Metal)
                      │
                      └── MCPClientManager
                            ├── Filesystem MCP Server
                            │     ├── read_file
                            │     ├── write_file
                            │     └── list_directory
                            │
                            ├── Notes MCP Server
                            │     ├── create_note
                            │     ├── list_notes
                            │     └── search_notes
                            │
                            └── Browser MCP Server
                                  ├── open_browser
                                  └── search_web
```

---

# Repository Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── mcp_client.py
│   │   ├── model_loader.py
│   │   └── routes.py
│   │
│   ├── mcp_servers/
│   │   ├── browser/
│   │   ├── filesystem/
│   │   └── notes/
│   │
│   ├── pyproject.toml
│   └── server.py
│
├── electron/
│   ├── main.js
│   └── preload.js
│
└── src/
    ├── App.jsx
    └── screens/
        └── ChatScreen.jsx
```

---

# Backend

The Python backend is responsible for:

- Loading and managing local GGUF language models
- Streaming responses using Server-Sent Events (SSE)
- Orchestrating the AI agent
- Discovering and invoking MCP tools
- Selecting the optimal compute strategy based on available hardware
- Exposing REST endpoints for the Electron application

## Backend Components

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Project configuration using **uv**. Includes FastAPI, LangChain, MCP, `llama-cpp-python`, Uvicorn, and `sse-starlette`. |
| `server.py` | FastAPI entry point. Configures CORS, mounts routers, and starts the Uvicorn server. |
| `app/config.py` | Loads configuration from `~/.desktop-companion/config.json`. |
| `app/model_loader.py` | Loads GGUF models using `llama.cpp`, selects the compute strategy, and streams generated tokens. |
| `app/agent.py` | LangChain orchestrator that builds prompts, invokes the model, and routes requests to MCP tools. |
| `app/mcp_client.py` | Starts MCP servers, discovers tools, and routes `call_tool()` requests. |
| `app/routes.py` | Defines backend API endpoints. |
| `mcp_servers/filesystem/` | Filesystem MCP server. |
| `mcp_servers/notes/` | Notes MCP server. |
| `mcp_servers/browser/` | Browser MCP server. |

---

# Electron

The Electron application serves as the desktop host and frontend for the AI assistant.

Its responsibilities include:

- Launching the Python backend
- Managing backend lifecycle
- Exposing backend IPC APIs
- Streaming chat responses
- Displaying model loading state

## Electron Components

| File | Responsibility |
|------|----------------|
| `electron/main.js` | Starts the FastAPI backend via Uvicorn, manages backend lifecycle, and exposes IPC handlers. |
| `electron/preload.js` | Exposes `window.electronAPI.backend.start()`, `stop()`, and `status()` to the renderer. |
| `src/screens/ChatScreen.jsx` | Connects to `/events`, streams `/chat` responses via SSE, and falls back to mock responses when the backend is unavailable. |
| `src/App.jsx` | Starts the backend automatically when entering the main application. |

---

# Model System

The project uses a **GGUF-only** inference pipeline powered by **llama.cpp**.

Unlike previous versions, there is no separate Transformers or bitsandbytes loading path. Every supported model is provided as a pre-quantized GGUF file and loaded through a single inference backend.

## Supported Models

| Model | Quantization | Approx. RAM | Best For |
|------|--------------|------------:|----------|
| `Qwen2.5-7B-Q8_0` | 8-bit | 7.5 GB | Highest quality |
| `Qwen2.5-7B-Q5_K_M` | 5-bit | 5.2 GB | Balanced performance |
| `Qwen2.5-7B-Q4_K_M` | 4-bit | 4.5 GB | Default recommendation |
| `Llama-3.1-8B-Q5_K_M` | 5-bit | 5.8 GB | Long-context tasks |
| `Llama-3.1-8B-Q4_K_M` | 4-bit | 5.0 GB | 128K context |
| `Phi-3.5-Mini-Q4_K_M` | 4-bit | 2.5 GB | Fast inference |
| `SmolLM2-1.7B-Q4_K_M` | 4-bit | 1.2 GB | Lightweight devices |

### Model Aliases

The loader supports user-friendly aliases that automatically resolve to the appropriate GGUF model.

Examples include:

- `auto`
- `glm-5.2`
- `qwen2.5-7b`
- `llama-3.1-8b`
- `phi-3.5-mini`
- `smollm2`

---

# Compute Strategy

The backend automatically selects the best execution mode based on the available hardware.

## NVIDIA CUDA

If CUDA is available:

- Full GPU offloading when sufficient VRAM is available
- Partial GPU offloading when VRAM is limited
- Automatic CPU fallback when necessary

```text
VRAM >= Model Requirement
    → Full GPU offload

VRAM >= 60% of Requirement
    → Partial GPU offload

Otherwise
    → CPU execution
```

## Apple Silicon

When Metal (MPS) is detected:

```text
n_gpu_layers = 1
```

This enables Metal acceleration through `llama.cpp`.

## CPU

If no compatible GPU is available:

```text
n_gpu_layers = 0
n_threads = min(cpu_cores, 8)
```

The thread count is capped to reduce CPU contention while maintaining good inference performance.

---

# Configuration Flow

Electron and the backend share a single configuration file.

```text
User completes onboarding
        │
        ▼
electron-store
writes config.json
        │
        ▼
~/.desktop-companion/config.json
        │
        ▼
Python Backend
(app/config.py)
        │
        ▼
load_config()
        │
        ▼
ModelLoader
resolves model alias
        │
        ▼
Loads GGUF model
        │
        ▼
Status events
(model_loading → model_ready)
        │
        ▼
Electron UI
updates loading state
```

Example:

```json
{
  "model": "glm-5.2",
  "ai_preference": "local"
}
```

---

# Backend Startup Flow

```text
Electron starts
      │
      ▼
backend.start()
      │
      ▼
Launch Uvicorn
      │
      ▼
FastAPI starts
      │
      ▼
Load configuration
      │
      ▼
Resolve model alias
      │
      ▼
Select compute strategy
      │
      ▼
Load GGUF model
      │
      ▼
Stream backend status
      │
      ▼
Chat UI becomes available
```

During startup, the chat interface displays a loading state until the backend emits the `model_ready` event.

---

# Runtime Communication

```text
Electron Renderer
        │
        │ GET /events (SSE)
        ▼
FastAPI Backend
        │
        ├── model_loading
        ├── model_ready
        └── status updates
        ▲
        │
        │ POST /chat
        │
        ▼
Token Stream (SSE)
        │
        ▼
ChatScreen renders tokens incrementally
```

---

# API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/events` | `GET` | Streams backend status events via SSE (`model_loading`, `model_ready`). |
| `/chat` | `POST` | Streams generated tokens to the frontend via SSE. |
| `/status` | `GET` | Returns the current backend state. |
| `/reset` | `POST` | Resets the current agent or session state. |

---

# MCP Servers

## Filesystem MCP Server

Available tools:

- `read_file`
- `write_file`
- `list_directory`

---

## Notes MCP Server

Available tools:

- `create_note`
- `list_notes`
- `search_notes`

---

## Browser MCP Server

Available tools:

- `open_browser`
- `search_web`

---

# Running the Project

## Install Backend Dependencies

```bash
cd backend
uv sync
```

## Start the Backend

```bash
uv run uvicorn server:app --port 8765
```

## Start the Electron Application

Open another terminal:

```bash
cd ..
npm run dev
```

---

# Development Notes

## Current Inference Pipeline

The project now exclusively uses **GGUF models** with **llama.cpp**.

The previous loading pipeline based on Transformers, bitsandbytes, PEFT, and Accelerate has been removed in favor of a single unified inference backend.

## Benefits

- Single model loading path
- Faster startup
- Lower memory usage
- No runtime quantization
- Simpler dependency management
- Automatic hardware-aware execution
- Consistent behavior across CPU, CUDA, and Apple Silicon

## Runtime Characteristics

- Electron automatically starts the backend.
- The frontend listens to backend status updates through Server-Sent Events.
- Chat responses are streamed token-by-token.
- Model configuration is shared between Electron and the backend.
- MCP servers are discovered and managed dynamically by the backend.
- Hardware acceleration is selected automatically without user configuration.