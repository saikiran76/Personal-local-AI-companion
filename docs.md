# Desktop Companion — Architecture & Documentation

> Version 0.1.0 | Local-first AI desktop assistant with MCP tool integration

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Electron Main Process](#electron-main-process)
4. [React Frontend](#react-frontend)
5. [Python Backend](#python-backend)
6. [Model Loading Pipeline](#model-loading-pipeline)
7. [Model Inference & Generation](#model-inference--generation)
8. [ReAct Agent Loop](#react-agent-loop)
9. [MCP Architecture](#mcp-architecture)
10. [SSE Streaming Protocol](#sse-streaming-protocol)
11. [Data Flow — End to End](#data-flow--end-to-end)
12. [File Map](#file-map)
13. [API Reference](#api-reference)
14. [Limitations](#limitations)
15. [Human-in-the-Loop Scenarios](#human-in-the-loop-scenarios)

---

## System Overview

Desktop Companion is a privacy-first desktop AI assistant that runs local GGUF language models on the user's device. It uses an Electron shell for the UI, a Python/FastAPI backend for inference, and MCP (Model Context Protocol) servers for tool integration. All processing happens on-device — no data leaves the machine.

**Core design principles:**
- Local-first: all inference and data processing on-device
- Privacy by default: no cloud dependencies for core functionality
- Modular MCP architecture: filesystem, notes, and browser tools as separate processes
- GPU-aware: automatically detects hardware and selects optimal model quantization

**Tech stack:**

| Layer | Technology |
|---|---|
| Desktop shell | Electron 33.x (frameless, custom titlebar) |
| Frontend | React 18 + Vite 6 |
| Backend | Python 3.12+ / FastAPI / Uvicorn |
| Inference | llama-cpp-python (GGUF models) |
| Tool protocol | MCP (JSON-RPC 2.0 over stdio) |
| Streaming | SSE (Server-Sent Events) |
| Config | electron-store (renderer) + JSON (backend) |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Electron Shell                           │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Renderer Process (React + Vite)                          │  │
│  │                                                           │  │
│  │  App.jsx ──► ChatScreen ──► parseSSE() ──► EventSource    │  │
│  │              LocalAIScreen ──► fetch /status              │  │
│  │              ImportModelModal ──► POST /models/import      │  │
│  │                                                           │  │
│  │  store.js ──► window.electronAPI ──► electron-store       │  │
│  └───────────────────────┬───────────────────────────────────┘  │
│                          │ IPC (contextBridge)                   │
│  ┌───────────────────────┴───────────────────────────────────┐  │
│  │  Main Process (electron/main.js)                          │  │
│  │  • Window management (minimize/maximize/close)            │  │
│  │  • Python backend lifecycle (spawn/kill/monitor)          │  │
│  │  • Zombie process cleanup (Windows netstat + taskkill)    │  │
│  │  • Native file dialog for .gguf import                    │  │
│  │  • electron-store persistence                             │  │
│  └───────────────────────┬───────────────────────────────────┘  │
│                          │ subprocess.Popen                      │
│  ┌───────────────────────┴───────────────────────────────────┐  │
│  │  Python Backend (FastAPI + Uvicorn, port 8765)            │  │
│  │                                                           │  │
│  │  server.py ──► routes.py ──► agent.py                     │  │
│  │                  │              │                          │  │
│  │                  │              ├── model_loader.py        │  │
│  │                  │              │     └── llama-cpp-python │  │
│  │                  │              │                          │  │
│  │                  │              └── mcp_client.py          │  │
│  │                  │                    │                    │  │
│  │                  │        ┌───────────┼───────────┐       │  │
│  │                  │        ▼           ▼           ▼       │  │
│  │                  │   ┌─────────┐ ┌─────────┐ ┌────────┐  │  │
│  │                  │   │FS Server│ │Notes Svr│ │Browser │  │  │
│  │                  │   │(stdio)  │ │(stdio)  │ │(stdio) │  │  │
│  │                  │   └─────────┘ └─────────┘ └────────┘  │  │
│  │                  │                                        │  │
│  │  config.py ──► ~/.desktop-companion/config.json          │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Electron Main Process

**File:** `electron/main.js`

The main process manages the application window, Python backend lifecycle, and IPC bridge.

### Window Configuration
- Frameless window (`frame: false`, `titleBarStyle: 'hidden'`) for custom titlebar
- Minimum size 800×600, default capped at 1200×800
- `contextIsolation: true`, `nodeIntegration: false` — secure renderer with preload script
- Dev mode: loads `http://localhost:5173` (Vite dev server)
- Production: loads `dist/index.html`

### Python Backend Management
- Spawns `python -m uvicorn server:app --host 127.0.0.1 --port 8765`
- On Windows: aggressive zombie process cleanup using `netstat -ano | findstr :8765` + `taskkill /F /T /PID`
- On Unix: `SIGTERM` with 3s timeout, then `SIGKILL`
- Port availability check before spawn to prevent binding errors

### IPC Channels

| Channel | Direction | Purpose |
|---|---|---|
| `window:minimize/maximize/close` | Renderer → Main | Window controls |
| `store:get/set/getAll/reset` | Bidirectional | electron-store access |
| `backend:start/stop/status` | Bidirectional | Python backend lifecycle |
| `model:import` | Bidirectional | Native file dialog + file copy to models dir |

**Preload bridge:** `electron/preload.js` exposes these as `window.electronAPI` via `contextBridge.exposeInMainWorld`.

---

## React Frontend

**Entry:** `src/App.jsx` → phase orchestrator

### Phase State Machine

```
LOADING → WELCOME → SETUP → MAIN
              ↑         ↑
              └─────────┘ (reset)
```

- **LOADING:** Reads persisted config from electron-store
- **WELCOME:** 5-step onboarding wizard (intro → capabilities → model → data → ready)
- **SETUP:** 5-step setup (username → assistant name → language → theme → model size)
- **MAIN:** Full application with sidebar navigation

### Component Hierarchy

```
App
├── TitleBar                    (custom window controls)
├── SidebarNavigation           (7 nav items + status indicator)
└── {activeScreen}
    ├── ChatScreen              (SSE streaming chat)
    │   ├── ImportModelModal    (drag-and-drop .gguf import)
    │   ├── ModelAdvisorCard    (upgrade recommendations)
    │   └── ThinkingIndicator   (animated dots)
    ├── LocalAIScreen           (hardware info, model registry)
    ├── MemoryScreen            (placeholder)
    ├── TasksScreen             (placeholder)
    ├── IntegrationsScreen      (placeholder)
    ├── AutomationsScreen       (placeholder)
    ├── PrivacyScreen           (placeholder)
    └── SettingsScreen          (config + reset)
```

### State Management
- No external state library (no Redux/Zustand)
- All state via React `useState` in `App`, propagated as props
- Config persisted via `src/store.js` → `electron-store` (Electron) or `localStorage` (browser dev)
- Backend status lifted from `ChatScreen` to `App` via callbacks

### SSE Connection Flow (ChatScreen)

On mount, `ChatScreen` opens `EventSource` to `http://127.0.0.1:8765/events`:

| Event | Handler |
|---|---|
| `connected` | Status message "Connected. Initializing..." |
| `model_loading` | `backendStatus = MODEL_LOADING` |
| `model_ready` | Store `modelInfo`, set `modelAvailable = true` |
| `backend_ready` | `backendStatus = READY`, fetch upgrade advice |
| `model_error` | `modelAvailable = false`, show error |
| `ping` | No-op keep-alive |

### Chat Message Flow

1. User types message, presses Enter
2. `streamingRef.current = true` (synchronous guard against double-send)
3. User message added to `messages[]`
4. `streamFromBackend()` creates empty assistant message, starts `fetch POST /chat`
5. Response body read via `ReadableStream` reader
6. `parseSSE()` extracts events (normalizes `\r\n` → `\n`, splits on `\n\n`)
7. Events processed: `thinking` → `token`* → `tool_call` → `tool_result` → `token`* → `done`
8. `finally` block resets `streamingRef`, `isStreaming`, `isThinking`, `activeTool`

### parseSSE Function

```javascript
function parseSSE(buffer) {
  const normalized = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const parts = normalized.split('\n\n');
  const complete = parts.slice(0, -1);
  const leftover = parts[parts.length - 1];
  // Parse event: and data: fields from each complete block
  return { events, leftover };
}
```

Handles sse-starlette 3.4.5's default `\r\n` separator by normalizing before parsing.

---

## Python Backend

**Entry:** `backend/server.py`

### FastAPI Configuration
- CORS: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`
- Port: `127.0.0.1:8765` (localhost only)
- Includes router from `backend/app/routes.py`

### ProactorEventLoop Monkey-Patch

On Windows, Python's `_ProactorReadPipeTransport._force_close()` references `self._empty_waiter` which doesn't exist, crashing the server when MCP subprocesses die. The patch injects the missing attribute before calling the original method.

### Configuration Storage

**File:** `backend/app/config.py`

Reads/writes `~/.desktop-companion/config.json`:

| Field | Default | Purpose |
|---|---|---|
| `ai_preference` | `"local"` | Triggers model loading |
| `model` | `"auto"` | Model name or alias |
| `model_path` | `None` | Explicit .gguf path |
| `user_name` | `"User"` | Display name |
| `assistant_name` | `"Companion"` | Assistant name |
| `mcp_filesystem/notes/browser` | `True` | MCP server toggles |

---

## Model Loading Pipeline

**File:** `backend/app/model_loader.py`

### Hardware Detection

Three detection functions run lazily (cached on first call):

| Function | Method | Fallback |
|---|---|---|
| VRAM | `nvidia-smi --query-gpu=memory.total` | 0 MB |
| RAM | `psutil.virtual_memory().total` | `wmic` (Windows) or `/proc/meminfo` |
| GPU name | `nvidia-smi --query-gpu=name` | `torch.backends.mps.is_available()` |

**Tier classification** (uses `max(vram_mb, ram_mb)`):
- `high`: ≥ 12,000 MB effective OR ≥ 6,000 MB VRAM
- `medium`: ≥ 6,000 MB effective
- `low`: < 6,000 MB effective

### GGUF Model Registry

Nine models across four families:

| Model | Context | RAM Required | Tool Capability |
|---|---|---|---|
| Qwen2.5-1.5B-Q4_K_M | 32K | 1,100 MB | good |
| Qwen2.5-3B-Q4_K_M | 32K | 2,200 MB | good |
| Qwen2.5-7B-Q4_K_M | 32K | 4,500 MB | good |
| Qwen2.5-7B-Q5_K_M | 32K | 5,200 MB | good |
| Qwen2.5-7B-Q8_0 | 32K | 7,500 MB | good |
| Llama-3.1-8B-Q4_K_M | 128K | 5,000 MB | good |
| Llama-3.1-8B-Q5_K_M | 128K | 5,800 MB | good |
| Phi-3.5-Mini-Q4_K_M | 32K | 2,500 MB | good |
| SmolLM2-1.7B-Q4_K_M | 8K | 1,200 MB | weak |

**Aliases:** `auto` → Qwen2.5-1.5B, `glm-5.2` → Qwen2.5-7B, `best` → Qwen2.5-7B-Q4_K_M

### Loading Flow

```
ModelLoader.load(model_name)
  │
  ├── detect_compute() → {device, vram, ram, cores, tier}
  │
  ├── _resolve_model_name(name)
  │     ├── Direct registry match
  │     ├── Local .gguf file scan (~/.desktop-companion/models/)
  │     ├── Alias lookup (case-insensitive)
  │     ├── Partial substring match
  │     └── _pick_by_tier() fallback
  │
  ├── _resolve_path(name)
  │     ├── Explicit model_path parameter
  │     ├── Registry filename in models dir
  │     ├── Any .gguf file (prefer largest)
  │     └── HuggingFace Hub download (hf_hub_download)
  │
  ├── GPU allocation
  │     ├── CUDA: n_gpu_layers = -1 (full) / 20 (partial) / 8 (minimal)
  │     ├── MPS: n_gpu_layers = 1
  │     └── CPU: n_gpu_layers = 0
  │
  ├── CPU threads: min(cores, 8)
  │
  ├── Context reduction: if RAM < 1.2× requirement → cap at 4096
  │
  └── LlamaModel(path, n_ctx, n_gpu_layers, n_threads)
        └── run_in_executor (non-blocking)
```

### Mock Fallback

When `llama-cpp-python` is not installed and no explicit model path is provided:
- 1.5s simulated load time
- Canned privacy-focused responses
- Backend stays operational for UI testing

---

## Model Inference & Generation

**File:** `backend/app/model_loader.py` — `generate()` / `_generate_stream()`

### Streaming Generation

```python
async def generate(self, messages, max_tokens=512):
    # Runs llama-cpp-python in background thread
    # Tokens placed into queue.Queue
    # Async generator reads from queue via run_in_executor
    yield token_string  # raw text tokens
    # Terminal: '{"finish_reason": "stop"}' or '{"error": "..."}'
```

- Uses `create_chat_completion(stream=True)` from llama-cpp-python
- Thread-safe queue bridges synchronous C++ inference to async Python
- Token queue blocks until next token is generated

### Token Processing in Agent

Tokens are consumed by the agent in two modes:

**Buffered mode** (tool-capable models):
1. All tokens collected into `token_buffer[]`
2. Full response accumulated in `full_response`
3. After generation: check if response is a tool call
4. If tool call → emit tool events, skip token streaming
5. If text → stream buffered tokens to client

**Streaming mode** (weak models / no tools):
1. Tokens yielded directly to client as they arrive
2. No tool call detection

---

## ReAct Agent Loop

**File:** `backend/app/agent.py`

The agent implements a ReAct (Reason + Act) pattern with up to 5 rounds of tool calling.

### System Prompt

```
You are a helpful desktop AI assistant running locally on the user's device.
You have access to tools for file system operations, note management, and browser automation.
Always prioritize user privacy — all processing happens locally.
Be concise, helpful, and proactive. Use tools when they would help accomplish the task.
```

### Loop Execution

```
chat(user_message)
  │
  ├── Append user message to conversation_history
  │
  ├── Check tool capability: get_model_tool_capability(model_name)
  │     └── If "weak" → _stream_without_tools() → return
  │
  └── for round in range(5):   # MAX_TOOL_ROUNDS = 5
        │
        ├── Build messages: system prompt + tool defs + history[-10:]
        │
        ├── Emit "thinking" event
        │
        ├── Buffered generation: collect all tokens
        │
        ├── Check for tool call: _extract_tool_call(full_response)
        │     ├── Brace-matching JSON parser
        │     ├── Validates "tool" and "arguments" keys
        │     └── Returns None if not a tool call
        │
        ├── Echo detection: _is_prompt_echo(full_response)
        │     └── 8 regex patterns, 3+ matches = echo (skip tool)
        │
        ├── IF valid tool call:
        │     ├── Emit tool_call event
        │     ├── mcp.call_tool(tool_name, tool_args)
        │     ├── Emit tool_result event
        │     ├── Append assistant + tool messages to history
        │     ├── _trim_history() (max 20 messages, keep last 10)
        │     └── continue → next round
        │
        └── ELSE (text response):
              ├── Validate: _is_valid_response()
              │     ├── > 10 chars
              │     ├── Not pure whitespace/punctuation
              │     ├── No character repeated >10 times
              │     └── Not a prompt echo
              │
              ├── IF valid: stream buffered tokens → emit done → return
              └── IF invalid: emit apology → break
```

### Tool Extraction

`_extract_tool_call(text)` uses brace-matching to find the outermost `{...}` containing `"tool"`:

1. Find `"tool"` in text
2. Scan backward for `{`
3. Track brace depth with string/escape awareness
4. Extract complete JSON object
5. `json.loads()` and validate keys

### Echo Detection

Eight compiled regex patterns detect when the model parrots system prompt:

| Pattern | Matches |
|---|---|
| `you have access to` | System prompt echo |
| `file system operations` | System prompt echo |
| `note management` | System prompt echo |
| `browser automation` | System prompt echo |
| `prioritize user privacy` | System prompt echo |
| `local processing` | System prompt echo |
| `tool.*json.*block` | JSON format echo |
| `\{"tool":\s*"tool_name"` | Template echo |

If 3+ patterns match, the response is treated as an echo and discarded.

---

## MCP Architecture

**File:** `backend/app/mcp_client.py`

### Design Decision

The official MCP SDK's `stdio_client` uses `asyncio.connect_read_pipe()` which crashes on Windows with ProactorEventLoop when subprocesses die. The solution: a custom `DirectStdioTransport` using `subprocess.Popen` with thread-based I/O.

### DirectStdioTransport

```python
class DirectStdioTransport:
    def __init__(self, command, args):
        self.process = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    async def send_request(self, method, params):
        request = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        await run_in_executor(self._write, request)
        return await run_in_executor(self._read_response)
```

### MCPClientManager

Manages connections to multiple MCP servers:

```
connect_all()
  │
  ├── For each enabled server config:
  │     ├── Spawn subprocess via DirectStdioTransport
  │     ├── Send "initialize" → receive capabilities
  │     ├── Send "tools/list" → receive tool schemas
  │     └── Store tools[tool_name] = {server, schema}
  │
  ├── get_tool_definitions_for_llm()
  │     └── Formats tools as human-readable text for system prompt
  │
  └── call_tool(name, args)
        ├── Route to correct server transport
        ├── Send "tools/call" with name + arguments
        └── Extract text from result.content[]
```

### JSON-RPC Protocol (newline-delimited)

**Handshake:**
```json
→ {"jsonrpc":"2.0","id":1,"method":"initialize"}
← {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"...","version":"0.1.0"}}}
```

**Tool discovery:**
```json
→ {"jsonrpc":"2.0","id":2,"method":"tools/list"}
← {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"...","description":"...","inputSchema":{...}}]}}
```

**Tool execution:**
```json
→ {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tool_name","arguments":{...}}}
← {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"..."}]}}
```

### MCP Servers

All servers use synchronous `for line in sys.stdin` reading (no asyncio pipe transport):

#### Filesystem Server (`mcp_servers/filesystem/server.py`)

| Tool | Parameters | Description |
|---|---|---|
| `read_file` | `path` (string) | Read file as UTF-8 |
| `write_file` | `path`, `content` | Create dirs, write content |
| `list_directory` | `path` | List entries sorted by name |

#### Notes Server (`mcp_servers/notes/server.py`)

Storage: `~/.desktop-companion/notes/*.md`

| Tool | Parameters | Description |
|---|---|---|
| `create_note` | `title`, `content` | Create markdown note file |
| `list_notes` | — | List all notes |
| `search_notes` | `query` | Case-insensitive substring search |
| `delete_note` | `note_id` | Delete note by ID |

#### Browser Server (`mcp_servers/browser/server.py`)

| Tool | Parameters | Description |
|---|---|---|
| `open_browser` | `url` | Open URL in default browser |
| `search_web` | `query` | Open Google search in browser |

---

## SSE Streaming Protocol

### Backend Event Types

| Event | Data | When |
|---|---|---|
| `connected` | — | SSE connection established |
| `model_loading` | `{model: "..."}` | Model load started |
| `model_ready` | `{name, device, tier, ...}` | Model loaded successfully |
| `model_error` | `{error: "..."}` | Model failed to load |
| `model_missing` | — | No model file found |
| `backend_ready` | `{tools: [...]}` | Agent initialized |
| `ping` | `{}` | Keep-alive (every 15s) |

### Chat Event Types

| Event | Data | Purpose |
|---|---|---|
| `thinking` | `{content: "Processing..."}` | Model is generating |
| `token` | `{content: "..."}` | Streamed text token |
| `clear` | `{}` | Reset last assistant message |
| `tool_call` | `{tool, arguments, message}` | Tool invocation started |
| `tool_result` | `{tool, result}` | Tool execution completed |
| `done` | `{}` | Response complete |
| `error` | `{error: "..."}` | Error occurred |

### Frontend SSE Handling

The `parseSSE` function in `ChatScreen.jsx`:
1. Normalizes `\r\n` → `\n` (sse-starlette 3.4.5 uses `\r\n`)
2. Splits on `\n\n` (SSE event delimiter)
3. Last part retained as `leftover` (incomplete event)
4. Each block parsed for `event:` and `data:` fields
5. Data field JSON-parsed into event object

---

## Data Flow — End to End

### Complete Request Lifecycle

```
1. User types "create a note called todo" in ChatScreen

2. sendMessage() → streamingRef.current = true → setIsStreaming(true)

3. streamFromBackend() → fetch POST /chat {"message": "create a note called todo"}

4. routes.py::chat() → agent.chat("create a note called todo")

5. AgentOrchestrator.chat():
   a. Append to conversation_history
   b. tool_cap = "good" → enter ReAct loop
   c. Round 1: build messages → LLM generates JSON tool call
   d. _extract_tool_call() → {"tool": "create_note", "arguments": {...}}
   e. _is_prompt_echo() → 0 matches → not echo
   f. Yield tool_call event → routes.py sends SSE "tool_call"
   g. mcp.call_tool("create_note", {title, content})
   h. DirectStdioTransport sends JSON-RPC to notes server
   i. Notes server creates ~/.desktop-companion/notes/todo.md
   j. Yield tool_result event → routes.py sends SSE "tool_result"
   k. Append assistant + tool messages to history
   l. Round 2: LLM sees tool result → generates text response
   m. Buffered tokens streamed as "token" events
   n. Yield done event → routes.py sends SSE "done"

6. Frontend receives events:
   - thinking → show animated dots
   - tool_call → show "Using create_note..." status bar
   - tool_result → clear tool status
   - token* → append to assistant message
   - done → clear thinking, isStreaming = false

7. User sees: tool call indicator → "Note created! I've created a note called todo."
```

### Model Import Lifecycle

```
1. User clicks Import in sidebar → ImportModelModal opens
2. User drags .gguf file or clicks to browse
3. POST /models/import → file streamed to ~/.desktop-companion/models/
4. Sets _pending_model_path, updates config.json
5. Frontend reconnects SSE → GET /events
6. /events detects _pending_model_path → unloads old model
7. Loads new model → sends model_ready → backend_ready
8. ChatScreen refreshes model info
```

---

## File Map

```
desktop-app/
├── electron/
│   ├── main.js                    # Electron main process, backend mgmt, IPC
│   └── preload.js                 # contextBridge → window.electronAPI
│
├── src/
│   ├── App.jsx                    # Root component, phase orchestrator
│   ├── store.js                   # Config persistence (electron-store / localStorage)
│   │
│   ├── screens/
│   │   ├── TitleBar.jsx           # Custom window controls
│   │   ├── WelcomeScreen.jsx      # 5-step onboarding wizard
│   │   ├── SetupScreen.jsx        # 5-step setup wizard
│   │   ├── ChatScreen.jsx         # Chat UI, SSE streaming, tool indicators
│   │   ├── LocalAIScreen.jsx      # Hardware info, model registry
│   │   ├── MemoryScreen.jsx       # Placeholder
│   │   ├── TasksScreen.jsx        # Placeholder
│   │   ├── IntegrationsScreen.jsx # Placeholder
│   │   ├── AutomationsScreen.jsx  # Placeholder
│   │   ├── PrivacyScreen.jsx      # Placeholder
│   │   ├── SettingsScreen.jsx     # Config + reset
│   │   └── screens.css            # Shared non-chat styles
│   │
│   ├── components/
│   │   ├── SidebarNavigation.jsx  # 7-item nav + status indicator
│   │   ├── ImportModelModal.jsx   # Drag-and-drop .gguf import
│   │   └── SidebarNavigation.css
│   │
│   └── styles/
│       ├── chat.css               # Chat layout + indicators
│       ├── setup.css              # Onboarding styles
│       ├── fonts.css              # Geist font @font-face
│       └── DESIGN-vercel.css      # Design tokens (colors, typography, spacing)
│
├── backend/
│   ├── server.py                  # FastAPI/Uvicorn entry, ProactorEventLoop patch
│   ├── pyproject.toml             # Python dependencies
│   │
│   ├── app/
│   │   ├── __init__.py
│   │   ├── model_loader.py        # Hardware detect, GGUF registry, llama.cpp inference
│   │   ├── agent.py               # ReAct loop, tool extraction, echo detection
│   │   ├── mcp_client.py          # DirectStdioTransport, MCPClientManager
│   │   ├── routes.py              # All HTTP/SSE endpoints
│   │   └── config.py              # JSON config read/write
│   │
│   └── mcp_servers/
│       ├── filesystem/
│       │   └── server.py          # read_file, write_file, list_directory
│       ├── notes/
│       │   └── server.py          # create_note, list_notes, search_notes, delete_note
│       └── browser/
│           └── server.py          # open_browser, search_web
│
├── vite.config.js                 # Vite build config (port 5173, @ alias)
├── package.json                   # Node dependencies + scripts
└── docs.md                        # This file
```

---

## API Reference

### Backend Endpoints (port 8765)

| Method | Path | Content-Type | Purpose |
|---|---|---|---|
| `GET` | `/health` | JSON | Health check with model info |
| `GET` | `/status` | JSON | Detailed hardware + model + MCP status |
| `GET` | `/events` | SSE | Lifecycle events (connected → model_loading → model_ready → backend_ready) |
| `POST` | `/chat` | SSE | Streaming chat (token/thinking/tool_call/tool_result/done events) |
| `POST` | `/reset` | JSON | Clear conversation history |
| `GET` | `/models/list` | JSON | List all available GGUF models |
| `POST` | `/models/import` | multipart | Upload .gguf model file |
| `POST` | `/models/switch` | JSON | Switch active model by name/path |
| `GET` | `/models/advise` | JSON | Get upgrade recommendation |
| `GET` | `/models/upgrade-options` | JSON | List models that fit current hardware |
| `POST` | `/shutdown` | JSON | Disconnect MCP, unload model |

---

## Limitations

### 1. No Email, Calendar, or External App Integration

The MCP servers only cover filesystem, notes, and browser. There are no integrations for:
- Email (Gmail, Outlook, etc.)
- Calendar (Google Calendar, Outlook)
- Messaging (Slack, Discord, Teams)
- Productivity suites (Notion, Trello, Asana)

**Impact:** The agent cannot draft or send emails, schedule meetings, or interact with any external application beyond opening a browser URL. When asked to "draft an email," it can only generate text in the chat — it cannot connect to an email client to actually send it.

### 2. Browser Automation is Window-Level Only

The browser MCP server uses Python's `webbrowser` module, which can only:
- Open a URL in the default browser
- Open a Google search page

It **cannot**:
- Interact with page elements (click, type, scroll)
- Read page content
- Fill forms
- Navigate within a web app
- Handle authentication flows

**Impact:** The agent can open a webpage but cannot automate any actions within it. Tasks like "book a flight" or "fill out this form" are impossible.

### 3. Filesystem Tools Are Basic

The filesystem MCP server provides only `read_file`, `write_file`, and `list_directory`. It lacks:
- File search / glob patterns
- Directory creation
- File copy/move/delete
- File metadata (size, modified date)
- Path traversal safety (no sandboxing)

**Impact:** The agent can read/write specific files but cannot search for files, manage directories, or perform file operations like "organize my downloads folder."

### 4. No Persistent Memory or Knowledge Base

The notes server stores markdown files, but there is:
- No vector database for semantic search
- No knowledge graph
- No embedding-based retrieval
- No conversation memory persistence (history resets on backend restart)

**Impact:** The agent cannot recall previous conversations or perform intelligent retrieval across notes. Each session starts fresh.

### 5. Limited Model Capability

- **SmolLM2-1.7B** (the smallest model) has `"weak"` tool capability — it cannot reliably generate structured JSON tool calls
- Even tool-capable models (Qwen2.5, Llama-3.1, Phi-3.5) sometimes produce malformed JSON or echo the system prompt
- The echo detection system (3+ pattern matches) may incorrectly reject valid responses
- Maximum 5 ReAct rounds per request — complex multi-step tasks may not complete

### 6. No Real-Time Data Access

The agent has no ability to:
- Access the internet (except opening a browser URL)
- Fetch live data (weather, news, stock prices)
- Connect to APIs
- Use web search results programmatically

**Impact:** The agent is limited to its training data and local files. It cannot answer "what's the weather today?" or "what's the latest news?"

### 7. No Multi-Turn Task Memory

The conversation history is capped at 20 messages (trimmed to 10). Long-running tasks that require tracking state across many interactions will lose context.

### 8. Windows-Only Zombie Process Handling

The aggressive process cleanup (`netstat` + `taskkill /F /T`) only works on Windows. On macOS/Linux, the backend may leave orphaned processes if the Electron app crashes.

### 9. Single-User, Single-Instance

The app assumes a single user on a single machine. There is no:
- Multi-user support
- Concurrent access protection
- Remote access capability
- Cloud sync

### 10. No Voice or Multimodal Input

The agent only accepts text input. There is no:
- Voice recognition / speech-to-text
- Image input / vision
- File attachment handling (beyond .gguf model import)

---

## Human-in-the-Loop Scenarios

The agent brings the human into the loop in these situations:

### Explicit Human Input Required

| Scenario | Why Human is Needed |
|---|---|
| "Draft an email" | Agent generates text but cannot send — human must copy-paste into email client |
| "Schedule a meeting" | No calendar integration — human must create the event manually |
| "Post to social media" | No social media API — human must post manually |
| "Send a message to..." | No messaging integration — human must send via their app |
| "Book a flight/hotel" | No booking API — human must complete the transaction |

### Tool Execution Requires Confirmation

| Scenario | Current Behavior |
|---|---|
| `write_file` | Agent writes without confirmation — no safety prompt |
| `create_note` | Agent creates without confirmation |
| `delete_note` | Agent deletes without confirmation — **dangerous** |
| `open_browser` | Agent opens URL without confirmation |

**Note:** The current implementation does NOT have a confirmation step for any tool execution. The agent executes tools autonomously. This is a safety concern — a future improvement should add human-in-the-loop confirmation for destructive actions.

### Model Selection and Configuration

| Scenario | Human Decision |
|---|---|
| First launch | Human chooses model size, data location, assistant name |
| Model upgrade | Advisor suggests upgrade, human decides whether to import |
| MCP server toggle | Human enables/disables servers in config |

### Task Completion Verification

| Scenario | Human Verification |
|---|---|
| Note creation | Human reviews the created note |
| File write | Human checks the written content |
| Search results | Human evaluates relevance |
| Generated text | Human reviews before using |

### Where the Agent Defers to Human

The agent's system prompt says "Use tools when they would help accomplish the task" — but in practice:
- The agent **always** tries to use tools if it detects a tool call in its response
- There is no "ask for permission" mechanism
- The agent cannot say "I need your permission to do this" — it either does it or doesn't detect the tool call
- If the model generates garbage JSON, the tool call fails silently and the agent falls back to text

### Recommended Human-in-the-Loop Improvements

1. **Tool execution confirmation:** Prompt user before `write_file`, `delete_note`, or any destructive action
2. **Email/calendar integration:** Add MCP servers for Gmail/Outlook/Google Calendar
3. **Browser automation:** Replace `webbrowser` with Playwright/Puppeteer for real automation
4. **File search:** Add glob/search capabilities to filesystem MCP server
5. **Conversation persistence:** Save/load chat history across sessions
6. **Multi-step task planning:** Allow agent to break down complex tasks and present a plan before execution
