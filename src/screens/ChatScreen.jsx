import { useState, useRef, useEffect, useCallback } from 'react';

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
  const events = [];
  const parts = buffer.split('\n\n');

  // Last part might be incomplete
  const complete = parts.slice(0, -1);
  const leftover = parts[parts.length - 1];

  for (const block of complete) {
    if (!block.trim()) continue;
    let eventType = 'message';
    let data = '';

    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        data = line.slice(6);
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

export default function ChatScreen({ config, onReset }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [backendStatus, setBackendStatus] = useState(BACKEND.DISCONNECTED);
  const [statusMessage, setStatusMessage] = useState('');
  const [modelInfo, setModelInfo] = useState(null);
  const [activeTool, setActiveTool] = useState(null); // currently executing tool
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const eventSourceRef = useRef(null);
  const abortRef = useRef(null);

  const userName = config?.userName || 'User';
  const assistantName = config?.assistantName || 'Companion';

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming, activeTool]);

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
      setStatusMessage(`Model loaded on ${data.device}`);
    });

    eventSource.addEventListener('backend_ready', (e) => {
      const data = JSON.parse(e.data);
      setBackendStatus(BACKEND.READY);
      setStatusMessage('');
      console.log('Backend ready, tools:', data.tools);
    });

    eventSource.addEventListener('model_error', (e) => {
      const data = JSON.parse(e.data);
      setBackendStatus(BACKEND.ERROR);
      setStatusMessage(`Model error: ${data.error}`);
    });

    eventSource.addEventListener('ping', () => {});

    eventSource.onerror = () => {
      setBackendStatus(BACKEND.DISCONNECTED);
      setStatusMessage('Backend not available. Using local mode.');
      eventSource.close();
    };
  }, []);

  const sendMessage = async (text) => {
    const trimmed = (text || input).trim();
    if (!trimmed || isStreaming) return;

    const userMsg = {
      id: Date.now(),
      role: 'user',
      content: trimmed,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsStreaming(true);

    if (backendStatus === BACKEND.READY) {
      await streamFromBackend(trimmed);
    } else {
      await mockResponse(trimmed);
    }
  };

  const streamFromBackend = async (message) => {
    const assistantMsg = {
      id: Date.now() + 1,
      role: 'assistant',
      content: '',
      toolCalls: [],
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, assistantMsg]);

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const response = await fetch(`${BACKEND_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
        signal: controller.signal,
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        sseBuffer += decoder.decode(value, { stream: true });
        const { events, leftover } = parseSSE(sseBuffer);
        sseBuffer = leftover;

        for (const event of events) {
          switch (event.type) {
            case 'token':
              // Append streaming token to the last assistant message
              if (event.data.content) {
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last.role === 'assistant') {
                    last.content += event.data.content;
                  }
                  return updated;
                });
              }
              break;

            case 'tool_call':
              // Show tool call indicator
              setActiveTool({
                name: event.data.tool,
                arguments: event.data.arguments,
                status: 'running',
              });
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                if (last.role === 'assistant') {
                  last.toolCalls = [
                    ...(last.toolCalls || []),
                    {
                      name: event.data.tool,
                      arguments: event.data.arguments,
                      status: 'running',
                    },
                  ];
                }
                return updated;
              });
              break;

            case 'tool_result':
              // Update tool call status
              setActiveTool(null);
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                if (last.role === 'assistant' && last.toolCalls?.length > 0) {
                  const toolCalls = [...last.toolCalls];
                  const lastTool = toolCalls[toolCalls.length - 1];
                  toolCalls[toolCalls.length - 1] = {
                    ...lastTool,
                    status: 'done',
                    result: event.data.result,
                  };
                  last.toolCalls = toolCalls;
                }
                return updated;
              });
              break;

            case 'done':
              // Stream complete
              break;

            case 'error':
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                if (last.role === 'assistant' && !last.content) {
                  last.content = `Error: ${event.data.error}`;
                }
                return updated;
              });
              break;
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Stream error:', err);
        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last.role === 'assistant' && !last.content) {
            last.content = 'Error connecting to backend. Please try again.';
          }
          return updated;
        });
      }
    } finally {
      setIsStreaming(false);
      setActiveTool(null);
      abortRef.current = null;
    }
  };

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
  };

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
          <button className="sidebar-new-chat" onClick={() => setMessages([])}>
            <span>+</span>
            <span>New chat</span>
          </button>
        </div>

        <div className="sidebar-conversations">
          <div className="sidebar-section-label">Today</div>
          {hasMessages ? (
            <div className="sidebar-conversation active">New conversation</div>
          ) : (
            <div className="sidebar-conversation" style={{ opacity: 0.5 }}>
              No conversations yet
            </div>
          )}
          <div className="sidebar-section-label" style={{ marginTop: 12 }}>Previous</div>
          <div className="sidebar-conversation" style={{ opacity: 0.5 }}>
            No previous chats
          </div>
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
              <span className="chat-header-btn" style={{ width: 'auto', padding: '0 10px', fontSize: 11, fontFamily: "'Geist Mono', monospace" }}>
                {modelInfo.device}
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
              {backendStatus === BACKEND.READY
                ? `I'm ${assistantName}, powered by ${modelInfo?.model || 'local AI'}. How can I help?`
                : `I'm ${assistantName}, your local AI assistant. How can I help today?`
              }
            </p>
            {statusMessage && backendStatus !== BACKEND.READY && (
              <p className="chat-empty-subtitle" style={{ fontSize: 12, color: 'var(--color-faint)', marginTop: -8 }}>
                {statusMessage}
              </p>
            )}
            <div className="chat-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s.label} className="chat-suggestion" onClick={() => handleSuggestion(s.label)}>
                  <div className="chat-suggestion-label">{s.label}</div>
                  {s.desc}
                </button>
              ))}
            </div>
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
                    {/* Tool calls indicator */}
                    {msg.toolCalls?.length > 0 && (
                      <div className="tool-calls">
                        {msg.toolCalls.map((tc, i) => (
                          <div key={i} className={`tool-call ${tc.status}`}>
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

                    <div className="message-timestamp">
                      {formatTime(msg.timestamp)}
                    </div>
                  </div>
                </div>
              ))}

              {/* Active tool indicator */}
              {activeTool && isStreaming && (
                <div className="tool-status-bar">
                  <span className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }} />
                  <span>Using {activeTool.name}...</span>
                </div>
              )}

              {/* Typing indicator */}
              {isStreaming && !activeTool && (
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
                  isModelLoading ? 'Model is loading...'
                  : activeTool ? `${activeTool.name} running...`
                  : `Message ${assistantName}...`
                }
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isModelLoading}
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
    </div>
  );
}
