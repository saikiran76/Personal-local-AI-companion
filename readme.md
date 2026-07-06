# Desktop Companion

A local-first AI desktop assistant built with **Electron**, **FastAPI**, **LangChain**, and **Model Context Protocol (MCP)**. The application runs a local language model (GLM-5.2), streams responses in real time using Server-Sent Events (SSE), and extends the model with MCP-powered tools for interacting with the filesystem, notes, and the web.

---

# Features

- 🖥️ Cross-platform desktop application powered by Electron
- 🤖 Local LLM inference with GLM-5.2
- ⚡ Real-time token streaming using Server-Sent Events (SSE)
- 🧠 LangChain-based agent orchestration
- 🔌 Model Context Protocol (MCP) integration
- 📂 Filesystem tools
- 📝 Notes management
- 🌐 Browser and web search tools
- ⚙️ Shared configuration between Electron and Python backend

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
                      │     └── GLM-5.2 (VRAM / CPU)
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

- Loading and managing the local language model
- Streaming responses using Server-Sent Events (SSE)
- Orchestrating the AI agent
- Discovering and invoking MCP tools
- Exposing REST endpoints for the Electron application

## Backend Components

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Project configuration using **uv**. Includes FastAPI, LangChain, MCP, Uvicorn, and `sse-starlette`. |
| `server.py` | FastAPI entry point. Configures CORS, mounts routers, and starts the Uvicorn server. |
| `app/config.py` | Loads configuration from `~/.desktop-companion/config.json`. |
| `app/model_loader.py` | Loads GLM-5.2 into VRAM or CPU and streams generated tokens. |
| `app/agent.py` | LangChain orchestrator that builds prompts, invokes the model, and routes requests to MCP tools. |
| `app/mcp_client.py` | Starts MCP servers, discovers tools, and routes `call_tool()` requests. |
| `app/routes.py` | Defines the backend API endpoints. |
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
loads selected model
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
  "model": "GLM-5.2",
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
Load GLM-5.2
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

## 1. Install Backend Dependencies

```bash
cd backend
uv sync
```

## 2. Start the Backend

```bash
uv run uvicorn server:app --port 8765
```

## 3. Start the Electron Application

Open another terminal:

```bash
cd ..
npm run dev
```

---

# Development Notes

- Electron automatically starts the backend during application initialization.
- The frontend listens to backend status updates through Server-Sent Events.
- Chat responses are streamed incrementally for a responsive user experience.
- Model configuration is shared between Electron and the backend through a common configuration file.
- MCP servers are launched and managed dynamically by the backend, allowing the agent to discover and invoke tools during runtime.