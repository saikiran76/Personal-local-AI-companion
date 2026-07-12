import { useState, useRef, useEffect, useCallback, Component } from 'react';
import { Upload, AlertCircle, Info, Search, Send, Mic, MicOff, Square } from 'lucide-react';
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
/* ------------------------------------------------------------------ */
/*  Error boundary — prevents one bad message from crashing the chat   */
/* ------------------------------------------------------------------ */

class MessageErrorBoundary extends Component {
  state = { hasError: false };
  static getDerivedStateFromError() { return { hasError: true }; }
  render() {
    if (this.state.hasError) {
      return <div className="message-error" style={{ padding: 12, color: 'var(--color-faint)', fontSize: 13 }}>Couldn't render this message.</div>;
    }
    return this.props.children;
  }
}

/* ------------------------------------------------------------------ */
/*  ComposeEmailForm — embedded in the chat stream                    */
/* ------------------------------------------------------------------ */

function ComposeEmailForm({ initialTo, initialSubject, initialBody, onDraft, onSend, onCancel, conversationId }) {
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
      const reqBody = { brief: brief.trim(), to, subject };
      if (conversationId) reqBody.conversation_id = conversationId;
      const res = await fetch(`${BACKEND_URL}/chat/draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reqBody),
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

/*  ReminderForm — embedded in the chat stream                         */
/* ------------------------------------------------------------------ */

function ReminderForm({ initialTitle, initialDate, initialTime, onSubmit, onCancel, conversationId }) {
  const [title, setTitle] = useState(initialTitle || '');
  const today = (() => { try { return new Date().toISOString().slice(0, 10); } catch { return ''; } })();
  const [dueDate, setDueDate] = useState(initialDate || today);
  const [dueTime, setDueTime] = useState(initialTime || '');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      await onSubmit({ title: title.trim(), due_date: dueDate, due_time: dueTime || undefined });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="compose-form">
      <div className="compose-header">
        <span className="compose-icon">⏰</span>
        <span className="compose-title">Set Reminder</span>
      </div>
      <div className="compose-fields">
        <div className="compose-field">
          <label>What</label>
          <input
            type="text"
            className="text-input"
            placeholder="e.g. Meeting with team"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div className="compose-field">
          <label>Date</label>
          <input
            type="date"
            className="text-input"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
          />
        </div>
        <div className="compose-field">
          <label>Time (optional)</label>
          <input
            type="time"
            className="text-input"
            value={dueTime}
            onChange={(e) => setDueTime(e.target.value)}
          />
        </div>
      </div>
      <div className="compose-actions">
        <button
          className="compose-btn compose-send"
          disabled={!title.trim() || submitting}
          onClick={handleSubmit}
        >
          {submitting ? 'Setting...' : 'Set Reminder'}
        </button>
        <button
          className="compose-btn compose-cancel"
          disabled={submitting}
          onClick={onCancel}
        >
          Cancel
        </button>
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
  const [pendingPermission, setPendingPermission] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const pendingClarifyRef = useRef(null);
  const pendingConfirmRef = useRef(null);
  const pendingPermissionRef = useRef(null);
  const permissionBusyRef = useRef(false);  // synchronously blocks double-clicks
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const eventSourceRef = useRef(null);
  const abortRef = useRef(null);
  const streamingRef = useRef(false);
  const assistantMsgIdRef = useRef(null);
  const sendMessageRef = useRef(null);

  // Voice state
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [ttsQueue, setTtsQueue] = useState([]);
  const ttsQueueRef = useRef([]);
  const ttsPlayingRef = useRef(false);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const audioContextRef = useRef(null);

  const userName = config?.userName || 'User';
  const assistantName = config?.assistantName || 'Luna';

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
      setStatusMessage(available ? '' : 'Model is not loaded. Import a valid merged .gguf model to enable AI.');
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
      setBackendStatus(BACKEND.ERROR);
      onBackendStatus?.(BACKEND.ERROR);
      setModelAvailable(false);
      onModelAvailable?.(false);
      setStatusMessage(`Model error: ${data.error}`);
    });

    eventSource.addEventListener('model_missing', (e) => {
      const data = JSON.parse(e.data);
      setModelAvailable(false);
      onModelAvailable?.(false);
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
    const hadPermission = pendingPermissionRef.current;

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
    setPendingPermission(null);
    pendingClarifyRef.current = null;
    pendingConfirmRef.current = null;
    pendingPermissionRef.current = null;

    if (isResponse && (hadClarify || hadConfirm || hadPermission)) {
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

  const streamFromBackend = async (message, response = null, resumeMessageId = null) => {
    let targetId;
    if (resumeMessageId) {
      // Resume existing assistant message (e.g., after permission grant)
      targetId = resumeMessageId;
      assistantMsgIdRef.current = resumeMessageId;
    } else {
      // Create new assistant message
      const assistantMsg = {
        id: Date.now() + 1,
        role: 'assistant',
        content: '',
        toolCalls: [],
        timestamp: new Date(),
        isThinking: true,
      };
      targetId = assistantMsg.id;
      assistantMsgIdRef.current = assistantMsg.id;
      setMessages((prev) => [...prev, assistantMsg]);
    }

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
                    msg.isThinking = false;
                    msg.statusText = '';
                    msg.content += event.data.content;
                  });
                }
                break;

              case 'thinking':
                setIsThinking(true);
                // Ensure assistant message exists for thinking dots
                updateMessage(targetId, (msg) => {
                  msg.isThinking = true;
                });
                break;

              case 'status':
                // Status text shows alongside thinking dots — don't clear isThinking
                updateMessage(targetId, (msg) => {
                  msg.statusText = event.data.content;
                });
                break;

              case 'perf_tip':
                updateMessage(targetId, (msg) => {
                  msg.perfTip = event.data.content;
                });
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
                  msg.isThinking = false;
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
                  msg.isThinking = false;
                  msg.statusText = '';
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
                  msg.isThinking = false;
                  msg.statusText = '';
                  msg.content = event.data.message;
                });
                break;

              case 'permission_request': {
                setIsThinking(false);
                setActiveTool(null);
                const pScope = event.data.arguments?.scope || event.data.tool || 'this';
                const pToolName = event.data.tool || '';
                const pToolArgs = event.data.arguments || {};
                // Find the last user message to re-send on approve
                let lastUserMsg = '';
                for (let i = messages.length - 1; i >= 0; i--) {
                  if (messages[i].role === 'user') { lastUserMsg = messages[i].content; break; }
                }
                console.log('[Permission] Captured lastUserMsg:', lastUserMsg ? lastUserMsg.slice(0, 50) : '(empty)');
                setPendingPermission({
                  message: event.data.message,
                  scope: pScope,
                  toolName: pToolName,
                  toolArgs: pToolArgs,
                  originalMessage: lastUserMsg,
                  assistantMessageId: targetId,
                });
                pendingPermissionRef.current = {
                  message: event.data.message,
                  scope: pScope,
                  toolName: pToolName,
                  toolArgs: pToolArgs,
                  originalMessage: lastUserMsg,
                  assistantMessageId: targetId,
                };
                updateMessage(targetId, (msg) => {
                  msg.isThinking = false;
                  msg.statusText = '';
                  msg.content = event.data.message;
                });
                break;
              }

              case 'compose_form': {
                setIsThinking(false);
                setActiveTool(null);
                const args = event.data.arguments || {};
                updateMessage(targetId, (msg) => {
                  msg.isThinking = false;
                  msg.statusText = '';
                  msg.composeForm = {
                    to: args.to || '',
                    subject: args.subject || '',
                    body: args.body || '',
                  };
                  msg.content = event.data.message || '';
                });
                break;
              }

              case 'reminder_form': {
                setIsThinking(false);
                setActiveTool(null);
                const rArgs = event.data.arguments || {};
                updateMessage(targetId, (msg) => {
                  msg.isThinking = false;
                  msg.statusText = '';
                  msg.reminderForm = {
                    title: rArgs.title || '',
                    due_date: rArgs.due_date || new Date().toISOString().slice(0, 10),
                    due_time: rArgs.due_time || '',
                  };
                  msg.content = event.data.message || 'Set a reminder:';
                });
                break;
              }

              case 'done':
                setIsThinking(false);
                setActiveTool(null);
                updateMessage(targetId, (msg) => {
                  msg.isThinking = false;
                });
                if (event.data.conversation_id && !activeConversationId) {
                  setActiveConversationId(event.data.conversation_id);
                }
                loadConversations();
                break;

              case 'error':
                setIsThinking(false);
                setActiveTool(null);
                updateMessage(targetId, (msg) => {
                  msg.isThinking = false;
                  msg.statusText = '';
                  if (!msg.content) msg.content = `Error: ${event.data.error}`;
                });
                break;

              case 'audio':
                // Queue TTS audio for sequential playback
                if (event.data.content) {
                  ttsQueueRef.current.push(event.data.content);
                  if (!ttsPlayingRef.current) {
                    playNextTts();
                  }
                }
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
      // Clean up any stuck isThinking messages from this stream
      const stuckId = assistantMsgIdRef.current;
      if (stuckId) {
        updateMessage(stuckId, (msg) => {
          if (msg.isThinking && !msg.content && !msg.composeForm && !msg.reminderForm) {
            msg.isThinking = false;
          }
        });
      }
      assistantMsgIdRef.current = null;
      abortRef.current = null;
    }
  };

  /* ---- Compose form actions (instant — no LLM round-trip) ---- */

  const handleComposeDraft = async (slots) => {
    try {
      const body = {
        tool_name: 'draft_email',
        tool_args: { to: slots.to, subject: slots.subject, body: slots.body },
      };
      if (activeConversationId) body.conversation_id = activeConversationId;
      const res = await fetch(`${BACKEND_URL}/tools/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const result = await res.json();
      const filename = result.filename || 'email.eml';
      if (result.conversation_id && !activeConversationId) {
        setActiveConversationId(result.conversation_id);
        loadConversations();
      }
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
      const body = {
        tool_name: 'open_email_client',
        tool_args: { to: slots.to, subject: slots.subject, body: slots.body },
      };
      if (activeConversationId) body.conversation_id = activeConversationId;
      await fetch(`${BACKEND_URL}/tools/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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

  /* ---- Reminder form handlers ---- */

  const handleReminderSubmit = async (slots) => {
    let scheduledMsg = '';
    try {
      const body = {
        tool_name: 'create_reminder',
        tool_args: { title: slots.title, due_date: slots.due_date, due_time: slots.due_time },
      };
      if (activeConversationId) body.conversation_id = activeConversationId;
      const resp = await fetch(`${BACKEND_URL}/tools/call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      console.log('[Reminder] /tools/call response:', data);
      // Extract scheduled status from the tool result
      try {
        const parsed = JSON.parse(data.result);
        scheduledMsg = parsed.scheduled || '';
      } catch { /* result may not be JSON */ }
    } catch { /* best-effort */ }

    const timeStr = slots.due_time ? ` at ${slots.due_time}` : '';
    const schedStr = scheduledMsg ? `\n\n${scheduledMsg}` : '';
    setMessages((prev) => [...prev, {
      id: Date.now(),
      role: 'assistant',
      content: `Reminder set: **${slots.title}** on ${slots.due_date}${timeStr}.${schedStr}`,
      toolCalls: [{ name: 'create_reminder', status: 'done', result: 'Created' }],
      timestamp: new Date(),
    }]);
    loadConversations();
  };

  const handleReminderCancel = () => {
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
    assistantMsgIdRef.current = assistantMsg.id;
    setMessages((prev) => [...prev, assistantMsg]);

    for (let i = 0; i < response.length; i += 3) {
      await new Promise((r) => setTimeout(r, 15));
      const chunk = response.slice(0, i + 3);
      updateMessage(assistantMsg.id, (msg) => { msg.content = chunk; });
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
    assistantMsgIdRef.current = assistantMsg.id;
    setMessages((prev) => [...prev, assistantMsg]);

    for (let i = 0; i < response.length; i += 3) {
      await new Promise((r) => setTimeout(r, 12));
      const chunk = response.slice(0, i + 3);
      updateMessage(assistantMsg.id, (msg) => { msg.content = chunk; });
    }
    setIsStreaming(false);
    streamingRef.current = false;
  };

  // Keep ref updated for voice callbacks
  sendMessageRef.current = sendMessage;

  // --- Voice: TTS playback ---
  const playNextTts = useCallback(async () => {
    if (ttsPlayingRef.current || ttsQueueRef.current.length === 0) return;
    ttsPlayingRef.current = true;
    const filePath = ttsQueueRef.current.shift();

    try {
      const audioUrl = filePath.startsWith('http')
        ? filePath
        : `file:///${filePath.replace(/\\/g, '/')}`;
      const audio = new Audio(audioUrl);
      await new Promise((resolve, reject) => {
        audio.onended = resolve;
        audio.onerror = reject;
        audio.play();
      });
    } catch (e) {
      console.warn('TTS playback failed:', e);
    } finally {
      ttsPlayingRef.current = false;
      if (ttsQueueRef.current.length > 0) {
        playNextTts();
      }
    }
  }, []);

  // --- Voice: WAV encoding from raw PCM ---
  const encodeWav = useCallback((channelData, sampleRate) => {
    const numChannels = 1;
    const bitsPerSample = 16;
    const numSamples = channelData.length;
    const bytesPerSample = bitsPerSample / 8;
    const blockAlign = numChannels * bytesPerSample;
    const dataSize = numSamples * blockAlign;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    const writeString = (offset, str) => {
      for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    };
    writeString(0, 'RIFF');
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * blockAlign, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitsPerSample, true);
    writeString(36, 'data');
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < numSamples; i++) {
      const sample = Math.max(-1, Math.min(1, channelData[i]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7FFF, true);
      offset += 2;
    }

    return new Blob([buffer], { type: 'audio/wav' });
  }, []);

  // --- Voice: Push-to-talk start ---
  const startRecording = useCallback(async () => {
    if (isRecording || isTranscribing || isStreaming) return;
    if (!navigator.mediaDevices?.getUserMedia) return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      audioContextRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);

      const audioBuffer = [];
      processor.onaudioprocess = (e) => {
        audioBuffer.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };

      source.connect(processor);
      processor.connect(ctx.destination);

      mediaRecorderRef.current = {
        stream, source, processor, audioBuffer,
        stop: () => {
          processor.disconnect();
          source.disconnect();
          stream.getTracks().forEach(t => t.stop());
          ctx.close();
        },
      };
      setIsRecording(true);
    } catch (err) {
      console.error('Mic access denied:', err);
      const errMsg = {
        id: Date.now(),
        role: 'assistant',
        content: err.name === 'NotAllowedError'
          ? "Microphone access denied. Please allow mic access in your browser settings and try again."
          : "Could not access microphone. Please check your audio device.",
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
    }
  }, [isRecording, isTranscribing, isStreaming]);

  // --- Voice: Push-to-talk stop + transcribe ---
  const stopRecording = useCallback(async () => {
    if (!isRecording || !mediaRecorderRef.current) return;
    setIsRecording(false);
    setIsTranscribing(true);

    const { audioBuffer, stop } = mediaRecorderRef.current;
    stop();

    if (audioBuffer.length === 0) {
      setIsTranscribing(false);
      return;
    }

    const totalLength = audioBuffer.reduce((sum, buf) => sum + buf.length, 0);
    const merged = new Float32Array(totalLength);
    let off = 0;
    for (const buf of audioBuffer) {
      merged.set(buf, off);
      off += buf.length;
    }

    const sampleRate = audioContextRef.current?.sampleRate || 44100;
    const wavBlob = encodeWav(merged, sampleRate);

    try {
      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const base64 = reader.result.split(',')[1];
          const resp = await fetch(`${BACKEND_URL}/voice/transcribe`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wav_base64: base64 }),
          });
          const data = await resp.json();
          if (data.error) {
            // Show transcription error as assistant message
            const errMsg = {
              id: Date.now(),
              role: 'assistant',
              content: `Voice error: ${data.error}`,
              timestamp: new Date(),
            };
            setMessages((prev) => [...prev, errMsg]);
          } else if (data.text && sendMessageRef.current) {
            sendMessageRef.current(data.text);
          } else {
            // Empty transcription — show feedback
            const emptyMsg = {
              id: Date.now(),
              role: 'assistant',
              content: "I couldn't hear anything. Try speaking closer to the microphone.",
              timestamp: new Date(),
            };
            setMessages((prev) => [...prev, emptyMsg]);
          }
        } catch (fetchErr) {
          console.error('Transcription request failed:', fetchErr);
          const errMsg = {
            id: Date.now(),
            role: 'assistant',
            content: "Voice service is starting up. Please try again in a moment.",
            timestamp: new Date(),
          };
          setMessages((prev) => [...prev, errMsg]);
        } finally {
          setIsTranscribing(false);
        }
      };
      reader.readAsDataURL(wavBlob);
    } catch (err) {
      console.error('Transcription failed:', err);
      setIsTranscribing(false);
    }
  }, [isRecording, encodeWav]);

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
            <Upload size={14} strokeWidth={2} />
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
                    <AlertCircle size={10} strokeWidth={2} style={{ marginRight: 4 }} />
                    no model
                  </>
                ) : modelInfo.quantization === 'mock' ? 'mock' : modelInfo.device}
              </span>
            )}
            <button className="chat-header-btn" title="Search">
              <Search size={14} strokeWidth={2} />
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
                  <Upload size={24} strokeWidth={1.5} />
                </div>
                <p className="chat-no-model-text">No model loaded yet</p>
                <p className="chat-no-model-hint">Import a .gguf model file to enable local AI inference</p>
                <button className="chat-no-model-btn" onClick={() => setShowImportModal(true)}>
                  <Upload size={14} strokeWidth={2} />
                  Import model
                </button>
              </div>
            )}

            {modelAvailable && modelAdvisor && (
              <div className="chat-advisor-banner">
                <div className="chat-advisor-icon">
                  <Info size={16} strokeWidth={1.5} />
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
                <MessageErrorBoundary key={msg.id}>
                <div className={`message message-${msg.role}`}>
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
                    {msg.content ? (
                      <div className="message-content">
                        {msg.content.split('\n').map((p, i) => (
                          <p key={i}>{p}</p>
                        ))}
                      </div>
                    ) : msg.isThinking ? (
                      <div className="message-content message-thinking">
                        <span className="typing-indicator">
                          <span className="typing-dot" />
                          <span className="typing-dot" />
                          <span className="typing-dot" />
                        </span>
                        {msg.statusText && (
                          <span className="message-status-text">{msg.statusText}</span>
                        )}
                      </div>
                    ) : msg.statusText ? (
                      <div className="message-content">
                        <span className="message-status-text">{msg.statusText}</span>
                      </div>
                    ) : null}

                    {/* Status indicator — italic muted text within same bubble */}
                    {msg.statusText && (
                      <div className="message-status-text">{msg.statusText}</div>
                    )}

                    {/* Perf tip — distinct card within same bubble */}
                    {msg.perfTip && (
                      <div className="perf-tip-card">{msg.perfTip}</div>
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
                        conversationId={activeConversationId}
                      />
                    )}

                    {/* Reminder form — embedded inline in the message */}
                    {msg.reminderForm && (
                      <ReminderForm
                        initialTitle={msg.reminderForm.title}
                        initialDate={msg.reminderForm.due_date}
                        initialTime={msg.reminderForm.due_time}
                        onSubmit={handleReminderSubmit}
                        onCancel={handleReminderCancel}
                        conversationId={activeConversationId}
                      />
                    )}

                    <div className="message-timestamp">
                      {formatTime(msg.timestamp)}
                    </div>
                  </div>
                </div>
                </MessageErrorBoundary>
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

              {/* Permission prompt */}
              {pendingPermission && !isStreaming && (
                <div className="confirm-prompt">
                  <div className="confirm-icon">🔒</div>
                  <div className="confirm-actions">
                    <button
                      className="confirm-btn confirm-yes"
                      disabled={permissionBusyRef.current}
                      onClick={async () => {
                        if (permissionBusyRef.current) return;
                        permissionBusyRef.current = true;
                        const pp = pendingPermission;
                        console.log('[Permission] Allow clicked, pp:', JSON.stringify(pp));
                        setPendingPermission(null);
                        pendingPermissionRef.current = null;
                        try {
                          const res = await fetch(`${BACKEND_URL}/permissions`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ scope: pp.scope, granted: true }),
                          });
                          console.log('[Permission] POST /permissions status:', res.status);
                          // Replay the original message with response="yes" to resume the pending tool call
                          if (pp.originalMessage) {
                            console.log('[Permission] Replaying with response=yes:', pp.originalMessage.slice(0, 50));
                            setIsStreaming(true);
                            streamingRef.current = true;
                            await streamFromBackend(pp.originalMessage, 'yes', pp.assistantMessageId);
                          } else {
                            console.log('[Permission] No originalMessage to replay!');
                          }
                        } catch (e) { console.log('[Permission] Error:', e); }
                        finally { permissionBusyRef.current = false; }
                      }}
                    >
                      Allow
                    </button>
                    <button
                      className="confirm-btn confirm-no"
                      disabled={permissionBusyRef.current}
                      onClick={() => {
                        if (permissionBusyRef.current) return;
                        permissionBusyRef.current = true;
                        const pp = pendingPermission;
                        setPendingPermission(null);
                        pendingPermissionRef.current = null;
                        setMessages((prev) => [...prev, {
                          id: Date.now(),
                          role: 'assistant',
                          content: `Permission denied for **${pp.scope}** access.`,
                          timestamp: new Date(),
                        }]);
                        setIsStreaming(false);
                        streamingRef.current = false;
                        setIsThinking(false);
                        permissionBusyRef.current = false;
                      }}
                    >
                      Deny
                    </button>
                  </div>
                </div>
              )}

              {/* Thinking indicator — removed, now part of the assistant message */}

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

              {/* Typing indicator — now part of the assistant message via msg.isThinking */}
              <div ref={messagesEndRef} />
            </div>
          </div>
        )}

        {/* Input Area */}
        <div className="chat-input-area">
          {isRecording && (
            <div className="voice-recording-bar">
              <div className="voice-recording-dot" />
              <span>Recording... release mic to send</span>
            </div>
          )}
          {isTranscribing && (
            <div className="voice-transcribing-bar">
              <div className="mic-spinner" />
              <span>Transcribing...</span>
            </div>
          )}
          <div className="chat-input-wrapper">
            <div className="chat-input-box">
              <textarea
                ref={textareaRef}
                className="chat-input"
                rows={1}
                placeholder={
                  isRecording ? 'Recording... release to send'
                  : isTranscribing ? 'Transcribing...'
                  : pendingClarify ? 'Type your answer above...'
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
              {input.trim() ? (
                <button
                  className="chat-send-btn"
                  disabled={isStreaming || isModelLoading}
                  onClick={() => sendMessage()}
                >
                  <Send size={14} strokeWidth={2} />
                </button>
              ) : (
                <button
                  className={`chat-mic-btn ${isRecording ? 'recording' : ''} ${isTranscribing ? 'transcribing' : ''}`}
                  disabled={isStreaming || isModelLoading || isTranscribing}
                  onMouseDown={startRecording}
                  onMouseUp={stopRecording}
                  onMouseLeave={() => { if (isRecording) stopRecording(); }}
                  onTouchStart={startRecording}
                  onTouchEnd={stopRecording}
                  title={isRecording ? 'Release to send' : isTranscribing ? 'Transcribing...' : 'Hold to speak'}
                >
                  {isTranscribing ? (
                    <div className="mic-spinner" />
                  ) : isRecording ? (
                    <Square size={12} strokeWidth={2} fill="currentColor" />
                  ) : (
                    <Mic size={14} strokeWidth={2} />
                  )}
                </button>
              )}
            </div>
            <div className="chat-input-footer">
              <span className="chat-input-hint">
                {backendStatus === BACKEND.READY && modelAvailable
                  ? `${assistantName} runs locally via ${modelInfo?.model || 'LLM'}. Press Enter to send.`
                  : backendStatus === BACKEND.READY
                  ? 'Import a valid merged .gguf model to enable local AI.'
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
