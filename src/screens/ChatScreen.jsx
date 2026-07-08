import { useState, useRef, useEffect, useCallback } from 'react';
import ImportModelModal from '../components/ImportModelModal';

const BACKEND_URL = 'http://127.0.0.1:8765';

const SUGGESTIONS = [
  { label: 'Organize my downloads folder', desc: 'Sort files by type and date' },
  { label: 'Summarize a document', desc: 'Paste text or point to a file' },
  { label: 'Draft a quick email', desc: 'Tell me who and what about' },
  { label: 'Help me plan my day', desc: 'Share your tasks and priorities' },
];

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function getInitial(name) {
  return name ? name.charAt(0).toUpperCase() : 'U';
}

function getAssistantInitial(name) {
  return name ? name.charAt(0).toUpperCase() : 'C';
}

const BACKEND = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  MODEL_LOADING: 'model_loading',
  READY: 'ready',
  ERROR: 'error',
};

/**
 * Parse SSE text stream into events.
 * SSE format: "event: name\ndata: json\n\n"
 */
function parseSSE(buffer) {
  const normalized = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const events = [];
  const parts = normalized.split('\n\n');
  const complete = parts.slice(0, -1);
  const leftover = parts[parts.length - 1];

  for (const block of complete) {
    if (!block.trim()) continue;
    let eventType = 'message';
    let data = '';

    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        data = line.slice(5).trim();
      }
    }

    if (data) {
      try {
        events.push({ type: eventType, data: JSON.parse(data) });
      } catch {
        events.push({ type: eventType, data: { raw: data } });
      }
    }
  }

  return { events, leftover };
}

/* ------------------------------------------------------------------ */
/*  ComposeEmailForm — embedded in the chat stream                    */
/* ------------------------------------------------------------------ */

function ComposeEmailForm({ initialTo, initialSubject, initialBody, onDraft, onSend, onCancel }) {
  const [to, setTo] = useState(initialTo || '');
  const [subject, setSubject] = useState(initialSubject || '');
  const [body, setBody] = useState(initialBody || '');
  const [brief, setBrief] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [sending, setSending] = useState(false);

  const handleLunaDraft = async () => {
    if (!brief.trim()) return;
    setDrafting(true);
    try {
      const res = await fetch(`${BACKEND_URL}/chat/draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brief: brief.trim(), to, subject }),
      });
      const data = await res.json();
      if (data.body) {
        setBody(data.body);
      }
    } catch {
      setBody('Draft generation failed. Please write your message manually.');
    } finally {
      setDrafting(false);
    }
  };

  const handleDraft = async () => {
    setSending(true);
    try {
      await onDraft({ to, subject, body });
    } finally {
      setSending(false);
    }
  };

  const handleSend = async () => {
    setSending(true);
    try {
      await onSend({ to, subject, body });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="compose-form">
      <div className="compose-header">
        <span className="compose-icon">✉️</span>
        <span className="compose-title">Draft Email</span>
      </div>
      <div className="compose-fields">
        <div className="compose-field">
          <label>To</label>
          <input
            type="email"
            placeholder="recipient@example.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
        <div className="compose-field">
          <label>Subject</label>
          <input
            type="text"
            placeholder="Email subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </div>
        <div className="compose-field compose-brief-field">
          <label>What should the email say?</label>
          <div className="compose-brief-row">
            <input
              type="text"
              placeholder="e.g. polite follow-up asking about the invoice"
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleLunaDraft();
                }
              }}
            />
            <button
              className="compose-btn compose-draft-btn"
              disabled={drafting || !brief.trim()}
              onClick={handleLunaDraft}
            >
              {drafting ? 'Writing...' : 'Luna draft it'}
            </button>
          </div>
        </div>
        <div className="compose-field">
          <label>Body</label>
          <textarea
            placeholder="Write your message, or use Luna draft it above..."
            rows={4}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </div>
      </div>
      <div className="compose-actions">
        <button
          className="compose-btn compose-save"
          disabled={sending || !to.trim()}
          onClick={handleDraft}
        >
          {sending ? 'Saving...' : 'Save Draft'}
        </button>
        <button
          className="compose-btn compose-send"
          disabled={sending || !to.trim()}
          onClick={handleSend}
        >
          Open in Mail App
        </button>
        <button
          className="compose-btn compose-cancel"
          disabled={sending}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      <div className="compose-draft-note">
        Drafts are saved locally in <code>~/.desktop-companion/drafts/</code>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ChatScreen                                                        */
/* ------------------------------------------------------------------ */

export default function ChatScreen({ config, onReset, onBackendStatus, onModelAvailable }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [backendStatus, setBackendStatus] = useState(BACKEND.DISCONNECTED);
  const [statusMessage, setStatusMessage] = useState('');
  const [modelInfo, setModelInfo] = useState(null);
  const [modelAvailable, setModelAvailable] = useState(false);
  const [activeTool, setActiveTool] = useState(null);
  const [isThinking, setIsThinking] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [modelAdvisor, setModelAdvisor] = useState(null);
  const [pendingClarify, setPendingClarify] = useState(null);
  const [pendingConfirm, setPendingConfirm] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const pendingClarifyRef = useRef(null);
  const pendingConfirmRef = useRef(null);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const eventSourceRef = useRef(null);
  const abortRef = useRef(null);
  const streamingRef = useRef(false);
  const assistantMsgIdRef = useRef(null);

  const userName = config?.userName || 'User';
  const assistantName = config?.assistantName || 'Companion';

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming, activeTool, isThinking]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px';
    }
  }, [input]);

  useEffect(() => {
    connectToBackend();
    return () => {
      eventSourceRef.current?.close();
      abortRef.current?.abort();
    };
  }, []);

  const connectToBackend = useCallback(() => {
    setBackendStatus(BACKEND.CONNECTING);
    onBackendStatus?.(BACKEND.CONNECTING);
    setStatusMessage('Connecting to backend...');

    const eventSource = new EventSource(`${BACKEND_URL}/events`);
    eventSourceRef.current = eventSource;

    eventSource.addEventListener('connected', () => {
      setStatusMessage('Connected. Initializing...');
    });

    eventSource.addEventListener('model_loading', (e) => {
      const data = JSON.parse(e.data);
      setBackendStatus(BACKEND.MODEL_LOADING);
      setStatusMessage(`Loading ${data.model}...`);
    });

    eventSource.addEventListener('model_ready', (e) => {
      const data = JSON.parse(e.data);
      setModelInfo(data);
      const available = data.model_available !== false;
      setModelAvailable(available);
      onModelAvailable?.(available);
      setStatusMessage(data.model_available === false
        ? 'No model file found. Import a .gguf model to enable AI.'
        : `Model loaded on ${data.device}`
      );
    });

    eventSource.addEventListener('backend_ready', (e) => {
      const data = JSON.parse(e.data);
      setBackendStatus(BACKEND.READY);
      onBackendStatus?.(BACKEND.READY);
      const available = data.model_available !== false;
      setModelAvailable(available);
      onModelAvailable?.(available);
      setStatusMessage('');
      console.log('Backend ready, tools:', data.tools, 'model_available:', data.model_available);

      fetch(`${BACKEND_URL}/models/advise`)
        .then((r) => r.json())
        .then((data) => {
          if (data.upgrade) {
            setModelAdvisor(data.upgrade);
          }
        })
        .catch(() => {});
    });

    eventSource.addEventListener('model_error', (e) => {
      const data = JSON.parse(e.data);
      setModelAvailable(false);
      onModelAvailable?.(false);
      setStatusMessage(`Model error: ${data.error}`);
    });

    eventSource.addEventListener('model_missing', (e) => {
      const data = JSON.parse(e.data);
      setModelAvailable(false);
      setStatusMessage(data.message || 'No model file found. Import a .gguf model to enable AI.');
    });

    eventSource.addEventListener('ping', () => {});

    eventSource.onerror = () => {
      setBackendStatus(BACKEND.DISCONNECTED);
      onBackendStatus?.(BACKEND.DISCONNECTED);
      setStatusMessage('Backend not available. Using local mode.');
      eventSource.close();
    };
  }, []);

  /* ---- Load conversation list from backend ---- */

  const loadConversations = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/conversations`);
      const data = await res.json();
      setConversations(data.conversations || []);
    } catch {
      // Backend not available yet — silent fail
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  /* ---- Load messages for a conversation ---- */

  const loadConversation = useCallback(async (convId) => {
    if (isStreaming) return;
    try {
      const res = await fetch(`${BACKEND_URL}/conversations/${convId}`);
      const data = await res.json();
      if (data.messages) {
        const mapped = data.messages.map((m, i) => ({
          id: Date.now() + i,
          role: m.role,
          content: m.content,
          toolCalls: m.role === 'tool_result' ? [{ name: m.tool_name || '', status: 'done', result: m.content }] : [],
          timestamp: new Date(m.created_at),
        }));
        setMessages(mapped);
        setActiveConversationId(convId);
      }
    } catch {
      // silent
    }
  }, [isStreaming]);

  /* ---- Helpers to update a specific message by ID ---- */

  const updateMessage = (msgId, updater) => {
    setMessages((prev) => {
      const updated = [...prev];
      const target = updated.find((m) => m.id === msgId);
      if (target) updater(target);
      return updated;
    });
  };

  /* ---- Send message ---- */

  const sendMessage = async (text, isResponse = false) => {
    const trimmed = (text || input).trim();
    if (!trimmed || streamingRef.current) return;
    streamingRef.current = true;

    const hadClarify = pendingClarifyRef.current;
    const hadConfirm = pendingConfirmRef.current;

    const userMsg = {
      id: Date.now(),
      role: 'user',
      content: trimmed,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsStreaming(true);
    setIsThinking(false);
    setActiveTool(null);

    setPendingClarify(null);
    setPendingConfirm(null);
    pendingClarifyRef.current = null;
    pendingConfirmRef.current = null;

    if (isResponse && (hadClarify || hadConfirm)) {
      await streamFromBackend(trimmed, trimmed);
      return;
    }

    if ((backendStatus === BACKEND.READY || backendStatus === BACKEND.ERROR) && !modelAvailable) {
      await noModelResponse(trimmed);
    } else if (backendStatus === BACKEND.READY) {
      await streamFromBackend(trimmed);
    } else {
      await mockResponse(trimmed);
    }
  };

  /* ---- SSE stream from backend ---- */

  const streamFromBackend = async (message, response = null) => {
    const assistantMsg = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
      toolCalls: [],
      timestamp: new Date(),
    };

    assistantMsgIdRef.current = assistantMsg.id;
    setMessages((prev) => [...prev, assistantMsg]);

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const body = { message };
      if (response) body.response = response;
      if (activeConversationId) body.conversation_id = activeConversationId;

      const res = await fetch(`${BACKEND_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      if (!res.body) throw new Error('Response body is null');

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = '';
      const targetId = assistantMsgIdRef.current;

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          sseBuffer += decoder.decode(value, { stream: true });
          const { events, leftover } = parseSSE(sseBuffer);
          sseBuffer = leftover;

          for (const event of events) {
            switch (event.type) {
              case 'token':
                setIsThinking(false);
                if (event.data.content) {
                  updateMessage(targetId, (msg) => {
                    msg.content += event.data.content;
                  });
                }
                break;

              case 'thinking':
                setIsThinking(true);
                break;

              case 'clear':
                updateMessage(targetId, (msg) => {
                  msg.content = '';
                  msg.toolCalls = [];
                });
                break;

              case 'tool_call':
                setIsThinking(false);
                setActiveTool({
                  name: event.data.tool,
                  arguments: event.data.arguments,
                  status: 'running',
                });
                updateMessage(targetId, (msg) => {
                  msg.toolCalls = [
                    ...(msg.toolCalls || []),
                    {
                      name: event.data.tool,
                      arguments: event.data.arguments,
                      status: 'running',
                    },
                  ];
                });
                break;

              case 'tool_result':
                setActiveTool(null);
                updateMessage(targetId, (msg) => {
                  if (msg.toolCalls?.length > 0) {
                    const toolCalls = [...msg.toolCalls];
                    const lastTool = toolCalls[toolCalls.length - 1];
                    toolCalls[toolCalls.length - 1] = {
                      ...lastTool,
                      status: 'done',
                      result: event.data.result,
                    };
                    msg.toolCalls = toolCalls;
                  }
                });
                break;

              case 'clarify':
                setIsThinking(false);
                setActiveTool(null);
                setPendingClarify({
                  message: event.data.message,
                  tool: event.data.tool,
                  arguments: event.data.arguments,
                });
                pendingClarifyRef.current = {
                  message: event.data.message,
                  tool: event.data.tool,
                  arguments: event.data.arguments,
                };
                updateMessage(targetId, (msg) => {
                  msg.content = event.data.message;
                });
                break;

              case 'confirm':
                setIsThinking(false);
                setActiveTool(null);
                setPendingConfirm({
                  message: event.data.message,
                  tool: event.data.tool,
                  arguments: event.data.arguments,
                });
                pendingConfirmRef.current = {
                  message: event.data.message,
                  tool: event.data.tool,
                  arguments: event.data.arguments,
                };
                updateMessage(targetId, (msg) => {
                  msg.content = event.data.message;
                });
                break;

              case 'compose_form': {
                setIsThinking(false);
                setActiveTool(null);
                const args = event.data.arguments || {};
                // Embed the compose form directly into the assistant message
                updateMessage(targetId, (msg) => {
                  msg.composeForm = {
                    to: args.to || '',
                    subject: args.subject || '',
                    body: args.body || '',
                  };
                  msg.content = event.data.message || '';
                });
                break;
              }

              case 'done':
                setIsThinking(false);
                setActiveTool(null);
                loadConversations();
                break;

              case 'error':
                setIsThinking(false);
                setActiveTool(null);
                updateMessage(targetId, (msg) => {
                  if (!msg.content) msg.content = `Error: ${event.data.error}`;
                });
                break;
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        updateMessage(assistantMsgIdRef.current, (msg) => {
          if (!msg.content) msg.content = 'Error connecting to backend. Please try again.';
        });
      }
    } finally {
      streamingRef.current = false;
      setIsStreaming(false);
      setIsThinking(false);
      setActiveTool(null);
      assistantMsgIdRef.current = null;
      abortRef.current = null;
    }
  };

  /* ---- Compose form actions (instant — no LLM round-trip) ---- */

  const handleComposeDraft = async (slots) => {
    try {
      const res = await fetch(`${BACKEND_URL}/tools/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool_name: 'draft_email',
          tool_args: { to: slots.to, subject: slots.subject, body: slots.body },
        }),
      });
      const result = await res.json();
      const filename = result.filename || 'email.eml';
      setMessages((prev) => [...prev, {
        id: Date.now(),
        role: 'assistant',
        content: `Draft saved locally as \`${filename}\`.\n\nOpen your email app to send it, or I can open your mail client now.`,
        toolCalls: [{ name: 'draft_email', status: 'done', result: `Saved as ${filename}` }],
        timestamp: new Date(),
      }]);
    } catch {
      setMessages((prev) => [...prev, {
        id: Date.now(),
        role: 'assistant',
        content: 'Error saving draft. Please try again.',
        timestamp: new Date(),
      }]);
    }
  };

  const handleComposeSend = async (slots) => {
    try {
      await fetch(`${BACKEND_URL}/tools/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool_name: 'open_email_client',
          tool_args: { to: slots.to, subject: slots.subject, body: slots.body },
        }),
      });
    } catch { /* mailto is best-effort */ }
    setMessages((prev) => [...prev, {
      id: Date.now(),
      role: 'assistant',
      content: 'Opened your email client. The draft is ready to send.',
      toolCalls: [{ name: 'open_email_client', status: 'done', result: 'Opened' }],
      timestamp: new Date(),
    }]);
  };

  const handleComposeCancel = () => {
    setMessages((prev) => [...prev, {
      id: Date.now(),
      role: 'assistant',
      content: 'Okay, cancelled.',
      timestamp: new Date(),
    }]);
  };

  /* ---- Mock / no-model responses ---- */

  const mockResponse = async () => {
    const responses = [
      "I'm your local AI assistant. The Python backend isn't connected yet, so I'm running in preview mode.",
      "Once the backend is running, I'll have access to file system tools, note management, and browser automation.",
      "Great question! The backend will process this using the local model running on your device.",
    ];
    const response = responses[Math.floor(Math.random() * responses.length)];

    const assistantMsg = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
      toolCalls: [],
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, assistantMsg]);

    for (let i = 0; i < response.length; i += 3) {
      await new Promise((r) => setTimeout(r, 15));
      const chunk = response.slice(0, i + 3);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last.role === 'assistant') last.content = chunk;
        return updated;
      });
    }
    setIsStreaming(false);
    streamingRef.current = false;
  };

  const noModelResponse = async (userMessage) => {
    const lower = userMessage.toLowerCase();
    let response;

    if (lower.includes('upload') || lower.includes('import') || lower.includes('model') || lower.includes('gguf')) {
      response = `I'd love to help with that! To get started, I need a model file first.\n\nClick the "Import model" button in the sidebar to upload a .gguf model file from your computer. Once imported, I'll be able to run entirely on your device with full privacy.`;
    } else if (lower.includes('hello') || lower.includes('hi') || lower.includes('hey')) {
      response = `Hey! I'm here, but I'm running without a model loaded yet. I can still guide you around the app.\n\nTo unlock my full capabilities, click "Import model" in the sidebar and select a .gguf model file.`;
    } else if (lower.includes('help') || lower.includes('what can you do')) {
      response = `Right now I'm in a limited state because no AI model is loaded. Once you import a model, I can:\n\n- Answer questions using a local LLM\n- Manage your files and notes\n- Automate browser tasks\n- All processing happens on your device\n\nClick "Import model" in the sidebar to get started.`;
    } else {
      response = `I received your message, but I can't process it without a model loaded.\n\nTo enable local AI inference, please import a .gguf model file:\n\n1. Click "Import model" in the sidebar\n2. Select a .gguf file from your computer\n3. The model will be loaded automatically\n\nOnce a model is imported, I'll be able to respond to your messages using local processing.`;
    }

    const assistantMsg = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
      toolCalls: [],
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, assistantMsg]);

    for (let i = 0; i < response.length; i += 3) {
      await new Promise((r) => setTimeout(r, 12));
      const chunk = response.slice(0, i + 3);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last.role === 'assistant') last.content = chunk;
        return updated;
      });
    }
    setIsStreaming(false);
    streamingRef.current = false;
  };

  /* ---- Input handling ---- */

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleSuggestion = (label) => sendMessage(label);

  const hasMessages = messages.length > 0;
  const isModelLoading = backendStatus === BACKEND.MODEL_LOADING || backendStatus === BACKEND.CONNECTING;

  return (
    <div className="chat-layout">
      {/* Sidebar */}
      <div className="chat-sidebar">
        <div className="sidebar-header">
          <div className="sidebar-brand">
            <div className="sidebar-brand-icon">&#x2728;</div>
            <span className="sidebar-brand-name">{assistantName}</span>
            <span className="sidebar-brand-version">v0.1</span>
          </div>
          <button className="sidebar-new-chat" onClick={() => { setMessages([]); setActiveConversationId(null); }}>
            <span>+</span>
            <span>New chat</span>
          </button>
          <button className="sidebar-import-model" onClick={() => setShowImportModal(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            <span>Import model</span>
          </button>
        </div>

        <div className="sidebar-conversations">
          {hasMessages && (
            <>
              <div className="sidebar-section-label">Current</div>
              <div className="sidebar-conversation active">
                {messages[0]?.content?.slice(0, 40) || 'New conversation'}
              </div>
            </>
          )}
          {conversations.length > 0 && (
            <>
              <div className="sidebar-section-label" style={{ marginTop: hasMessages ? 12 : 0 }}>Previous</div>
              {conversations.map((conv) => (
                <div
                  key={conv.id}
                  className={`sidebar-conversation ${activeConversationId === conv.id ? 'active' : ''}`}
                  onClick={() => loadConversation(conv.id)}
                >
                  {conv.title || `Conversation ${conv.id}`}
                </div>
              ))}
            </>
          )}
          {!hasMessages && conversations.length === 0 && (
            <div className="sidebar-conversation" style={{ opacity: 0.5 }}>
              No conversations yet
            </div>
          )}
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-user" onClick={onReset} title="Reset & start over" style={{ cursor: 'pointer' }}>
            <div className="sidebar-avatar">{getInitial(userName)}</div>
            <span className="sidebar-user-name">{userName}</span>
          </div>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="chat-main">
        <div className="chat-header">
          <span className="chat-header-title">{assistantName}</span>
          <div className="chat-header-actions">
            {modelInfo && (
              <span
                className={`chat-header-btn ${!modelAvailable ? 'chat-header-btn-warning' : ''}`}
                style={{ width: 'auto', padding: '0 10px', fontSize: 11, fontFamily: "'Geist Mono', monospace" }}
                title={!modelAvailable ? 'No model loaded - click Import model in sidebar' : `Running on ${modelInfo.device}`}
              >
                {!modelAvailable ? (
                  <>
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}>
                      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    no model
                  </>
                ) : modelInfo.quantization === 'mock' ? 'mock' : modelInfo.device}
              </span>
            )}
            <button className="chat-header-btn" title="Search">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
            </button>
          </div>
        </div>

        {/* Loading State */}
        {isModelLoading && !hasMessages ? (
          <div className="chat-empty">
            <div className="chat-empty-icon">&#x2728;</div>
            <h2 className="chat-empty-title">Starting up</h2>
            <p className="chat-empty-subtitle">{statusMessage}</p>
            <div style={{ marginTop: 16 }}>
              <div className="spinner" style={{ width: 24, height: 24 }} />
            </div>
          </div>
        ) : !hasMessages ? (
          /* Empty State */
          <div className="chat-empty">
            <div className="chat-empty-icon">&#x2728;</div>
            <h2 className="chat-empty-title">Hi, {userName}</h2>
            <p className="chat-empty-subtitle">
              {backendStatus === BACKEND.READY && modelAvailable
                ? `I'm ${assistantName}, powered by ${modelInfo?.model || 'local AI'}. How can I help?`
                : backendStatus === BACKEND.READY && !modelAvailable
                ? `I'm ${assistantName}, ready to help. Import a model to get started.`
                : `I'm ${assistantName}, your local AI assistant. How can I help today?`
              }
            </p>
            {statusMessage && backendStatus !== BACKEND.READY && (
              <p className="chat-empty-subtitle" style={{ fontSize: 12, color: 'var(--color-faint)', marginTop: -8 }}>
                {statusMessage}
              </p>
            )}

            {(backendStatus === BACKEND.READY || backendStatus === BACKEND.ERROR) && !modelAvailable && (
              <div className="chat-no-model-prompt">
                <div className="chat-no-model-icon">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
                  </svg>
                </div>
                <p className="chat-no-model-text">No model loaded yet</p>
                <p className="chat-no-model-hint">Import a .gguf model file to enable local AI inference</p>
                <button className="chat-no-model-btn" onClick={() => setShowImportModal(true)}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
                  </svg>
                  Import model
                </button>
              </div>
            )}

            {modelAvailable && modelAdvisor && (
              <div className="chat-advisor-banner">
                <div className="chat-advisor-icon">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
                  </svg>
                </div>
                <div className="chat-advisor-text">
                  <span className="chat-advisor-title">Model upgrade recommended</span>
                  <span className="chat-advisor-desc">
                    {modelAdvisor.reason}
                    {' '}Switch to <strong>{modelAdvisor.recommended_model}</strong> for better results.
                  </span>
                </div>
                <button className="chat-no-model-btn" onClick={() => setShowImportModal(true)} style={{ marginLeft: 'auto', flexShrink: 0 }}>
                  Upgrade model
                </button>
              </div>
            )}

            {modelAvailable && (
              <div className="chat-suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s.label} className="chat-suggestion" onClick={() => handleSuggestion(s.label)}>
                    <div className="chat-suggestion-label">{s.label}</div>
                    {s.desc}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          /* Messages */
          <div className="chat-messages">
            <div className="chat-messages-inner">
              {messages.map((msg) => (
                <div key={msg.id} className={`message message-${msg.role}`}>
                  <div className="message-avatar">
                    {msg.role === 'assistant'
                      ? getAssistantInitial(assistantName)
                      : getInitial(userName)}
                  </div>
                  <div className="message-body">
                    {/* Tool calls — prominent badge style */}
                    {msg.toolCalls?.length > 0 && (
                      <div className="tool-calls">
                        {msg.toolCalls.map((tc, i) => (
                          <div key={i} className={`tool-call tool-call-${tc.status}`}>
                            <span className="tool-call-icon">
                              {tc.status === 'running' ? '⚙️' : '✅'}
                            </span>
                            <span className="tool-call-name">{tc.name}</span>
                            {tc.status === 'running' && (
                              <span className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }} />
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Message content */}
                    {msg.content && (
                      <div className="message-content">
                        {msg.content.split('\n').map((p, i) => (
                          <p key={i}>{p}</p>
                        ))}
                      </div>
                    )}

                    {/* Compose form — embedded inline in the message */}
                    {msg.composeForm && (
                      <ComposeEmailForm
                        initialTo={msg.composeForm.to}
                        initialSubject={msg.composeForm.subject}
                        initialBody={msg.composeForm.body}
                        onDraft={handleComposeDraft}
                        onSend={handleComposeSend}
                        onCancel={handleComposeCancel}
                      />
                    )}

                    <div className="message-timestamp">
                      {formatTime(msg.timestamp)}
                    </div>
                  </div>
                </div>
              ))}

              {/* Clarify prompt */}
              {pendingClarify && !isStreaming && (
                <div className="clarify-prompt">
                  <div className="clarify-icon">💬</div>
                  <div className="clarify-actions">
                    <input
                      className="clarify-input"
                      type="text"
                      placeholder="Type your answer..."
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          sendMessage(input, true);
                        }
                      }}
                      autoFocus
                    />
                    <button
                      className="clarify-send-btn"
                      disabled={!input.trim()}
                      onClick={() => sendMessage(input, true)}
                    >
                      Send
                    </button>
                  </div>
                </div>
              )}

              {/* Confirm prompt */}
              {pendingConfirm && !isStreaming && (
                <div className="confirm-prompt">
                  <div className="confirm-icon">⚠️</div>
                  <div className="confirm-actions">
                    <button
                      className="confirm-btn confirm-yes"
                      onClick={() => sendMessage('yes', true)}
                    >
                      Confirm
                    </button>
                    <button
                      className="confirm-btn confirm-no"
                      onClick={() => sendMessage('no', true)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {/* Thinking indicator */}
              {isThinking && isStreaming && !activeTool && (
                <div className="message message-assistant">
                  <div className="message-avatar">
                    {getAssistantInitial(assistantName)}
                  </div>
                  <div className="message-body">
                    <div className="message-content">
                      <div className="typing-indicator">
                        <div className="typing-dot" />
                        <div className="typing-dot" />
                        <div className="typing-dot" />
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Active tool indicator — prominent bar */}
              {activeTool && isStreaming && (
                <div className="tool-status-bar">
                  <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
                  <span className="tool-status-name">{activeTool.name}</span>
                  <span className="tool-status-dots">
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                  </span>
                </div>
              )}

              {/* Typing indicator */}
              {isStreaming && !isThinking && !activeTool && (
                <div className="message message-assistant">
                  <div className="message-avatar">
                    {getAssistantInitial(assistantName)}
                  </div>
                  <div className="message-body">
                    <div className="message-content">
                      <div className="typing-indicator">
                        <div className="typing-dot" />
                        <div className="typing-dot" />
                        <div className="typing-dot" />
                      </div>
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          </div>
        )}

        {/* Input Area */}
        <div className="chat-input-area">
          <div className="chat-input-wrapper">
            <div className="chat-input-box">
              <textarea
                ref={textareaRef}
                className="chat-input"
                rows={1}
                placeholder={
                  pendingClarify ? 'Type your answer above...'
                  : pendingConfirm ? 'Click Confirm or Cancel above...'
                  : isModelLoading ? 'Model is loading...'
                  : isThinking ? 'Thinking...'
                  : activeTool ? `${activeTool.name} running...`
                  : `Message ${assistantName}...`
                }
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isModelLoading || !!pendingClarify || !!pendingConfirm}
              />
              <button
                className="chat-send-btn"
                disabled={!input.trim() || isStreaming || isModelLoading}
                onClick={() => sendMessage()}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"/>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              </button>
            </div>
            <div className="chat-input-footer">
              <span className="chat-input-hint">
                {backendStatus === BACKEND.READY
                  ? `${assistantName} runs locally via ${modelInfo?.model || 'LLM'}. Press Enter to send.`
                  : `${assistantName} in preview mode — start the Python backend for full capabilities.`
                }
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Import Model Modal */}
      {showImportModal && (
        <ImportModelModal
          onClose={() => setShowImportModal(false)}
          onImported={(models, modelLoaded) => {
            console.log('Models imported:', models, 'model_loaded:', modelLoaded);
            setShowImportModal(false);
            setMessages([]);
            setModelInfo(null);
            setModelAvailable(false);
            setBackendStatus(BACKEND.CONNECTING);
            setStatusMessage('Model imported. Reloading...');
            eventSourceRef.current?.close();
            setTimeout(() => connectToBackend(), 500);
          }}
        />
      )}
    </div>
  );
}
