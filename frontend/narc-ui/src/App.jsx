import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";

const DEFAULT_BACKEND_URL =
  "https://rag-backend.orangeglacier-b4beb8b5.uaenorth.azurecontainerapps.io";

const STARTERS = [
  {
    eyebrow: "Admissions",
    prompt: "What documents do I need for undergraduate admission?",
  },
  {
    eyebrow: "Campus life",
    prompt: "Tell me about hostel availability and accommodation at NUST.",
  },
  {
    eyebrow: "Support",
    prompt: "How can I contact the registrar's office?",
  },
  {
    eyebrow: "Funding",
    prompt: "What are the scholarship criteria for undergraduate students?",
  },
];

function Icon({ name, size = 18 }) {
  const props = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };

  if (name === "plus") {
    return (
      <svg {...props}>
        <path d="M12 5v14M5 12h14" />
      </svg>
    );
  }
  if (name === "settings") {
    return (
      <svg {...props}>
        <path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 5v4M6 15v4" />
      </svg>
    );
  }
  if (name === "arrow") {
    return (
      <svg {...props}>
        <path d="M5 12h14M13 6l6 6-6 6" />
      </svg>
    );
  }
  if (name === "spark") {
    return (
      <svg {...props}>
        <path d="M12 2.8c.75 4.05 3.2 6.5 7.2 7.2-4 .75-6.45 3.2-7.2 7.2-.75-4-3.2-6.45-7.2-7.2 4-.7 6.45-3.15 7.2-7.2Z" />
        <path d="M19 16.5c.25 1.55 1.2 2.5 2.7 2.75-1.5.25-2.45 1.2-2.7 2.75-.3-1.55-1.2-2.5-2.75-2.75 1.55-.25 2.45-1.2 2.75-2.75Z" />
      </svg>
    );
  }
  if (name === "chevron") {
    return (
      <svg {...props}>
        <path d="m7 9 5 5 5-5" />
      </svg>
    );
  }
  if (name === "external") {
    return (
      <svg {...props}>
        <path d="M14 5h5v5M19 5l-8 8" />
        <path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
      </svg>
    );
  }
  if (name === "copy") {
    return (
      <svg {...props}>
        <rect x="8" y="8" width="11" height="11" rx="2" />
        <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
      </svg>
    );
  }
  if (name === "check") {
    return (
      <svg {...props}>
        <path d="m5 12 4 4L19 6" />
      </svg>
    );
  }
  if (name === "close") {
    return (
      <svg {...props}>
        <path d="M6 6l12 12M18 6 6 18" />
      </svg>
    );
  }
  if (name === "link") {
    return (
      <svg {...props}>
        <path d="M10.5 13.5 13.5 10" />
        <path d="M7.4 16.6 5.8 18.2a3 3 0 0 1-4.2-4.2l3.2-3.2A3 3 0 0 1 9 10.8" />
        <path d="m16.6 7.4 1.6-1.6A3 3 0 0 1 22.4 10l-3.2 3.2a3 3 0 0 1-4.2 0" />
      </svg>
    );
  }
  return null;
}

function formatTimer(seconds) {
  if (!seconds || seconds <= 0) return "memory idle";
  const minutes = Math.ceil(seconds / 60);
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h memory`;
  return `${minutes}m memory`;
}

function sourceName(source) {
  if (source?.title) return source.title;
  const raw = source?.source_url || source?.url;
  if (!raw) return "NUST source";
  try {
    return new URL(raw).hostname.replace(/^www\./, "");
  } catch {
    return raw;
  }
}

function matchLabel(score) {
  if (score == null || Number.isNaN(Number(score))) return null;
  const number = Number(score);
  const percentage = Math.round(Math.max(0, Math.min(1, number)) * 100);
  return `${percentage}% match`;
}

export default function App() {
  const [backendUrl, setBackendUrl] = useState(DEFAULT_BACKEND_URL);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expandedSources, setExpandedSources] = useState({});
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [copiedIndex, setCopiedIndex] = useState(null);
  const [expiresIn, setExpiresIn] = useState(0);
  const scrollRef = useRef(null);
  const textareaRef = useRef(null);

  const hasMessages = messages.length > 0;
  const memoryLabel = useMemo(() => formatTimer(expiresIn), [expiresIn]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, loading]);

  useEffect(() => {
    if (!expiresIn) return undefined;
    const timer = window.setInterval(() => {
      setExpiresIn((current) => Math.max(0, current - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [Boolean(expiresIn)]);

  useEffect(() => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "0px";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 156)}px`;
  }, [input]);

  async function sendMessage(overrideText) {
    const text = (typeof overrideText === "string" ? overrideText : input).trim();
    if (!text || loading) return;

    const targetUrl = (backendUrl || DEFAULT_BACKEND_URL).trim();

    setError(null);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const response = await fetch(`${targetUrl.replace(/\/$/, "")}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });

      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        const messageText =
          payload?.detail ||
          payload?.error ||
          payload?.message ||
          (typeof payload === "string" ? payload : "");
        throw new Error(
          `${response.status} ${response.statusText}${
            messageText ? ` — ${String(messageText).slice(0, 300)}` : ""
          }`
        );
      }

      if (!payload || typeof payload !== "object") {
        throw new Error("The backend returned an invalid response.");
      }

      setSessionId(payload.session_id ?? sessionId);
      setExpiresIn(Number(payload.expires_in_seconds) || 0);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: payload.answer || "",
          sources: Array.isArray(payload.sources) ? payload.sources : [],
        },
      ]);
    } catch (event) {
      setError(event?.message || "Request failed");
    } finally {
      setLoading(false);
    }
  }

  function resetSession() {
    const oldSession = sessionId;
    const targetUrl = (backendUrl || DEFAULT_BACKEND_URL).trim();

    setSessionId(null);
    setMessages([]);
    setInput("");
    setError(null);
    setExpandedSources({});
    setExpiresIn(0);
    setCopiedIndex(null);

    if (oldSession) {
      fetch(`${targetUrl.replace(/\/$/, "")}/chat/${encodeURIComponent(oldSession)}`, {
        method: "DELETE",
      }).catch(() => {});
    }
  }

  function toggleSources(index) {
    setExpandedSources((prev) => ({ ...prev, [index]: !prev[index] }));
  }

  async function copyAnswer(text, index) {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIndex(index);
      window.setTimeout(() => setCopiedIndex(null), 1400);
    } catch {
      // Clipboard can be blocked in some embedded browsers; failing silently
      // keeps the chat flow uninterrupted.
    }
  }

  return (
    <div className="site-frame">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <div className="noise" />

      <aside className="side-rail">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <span />
            <span />
          </div>
          <div>
            <div className="brand-name">narc<span>°</span></div>
            <div className="brand-subtitle">NUST knowledge interface</div>
          </div>
        </div>

        <button className="new-chat-button" type="button" onClick={resetSession}>
          <span>New conversation</span>
          <Icon name="plus" size={17} />
        </button>

        <div className="rail-section">
          <div className="rail-kicker">Try asking</div>
          <div className="rail-prompts">
            {STARTERS.slice(0, 3).map((starter) => (
              <button
                key={starter.prompt}
                className="rail-prompt"
                type="button"
                onClick={() => sendMessage(starter.prompt)}
                disabled={loading}
              >
                <span>{starter.eyebrow}</span>
                <p>{starter.prompt}</p>
              </button>
            ))}
          </div>
        </div>

        <div className="rail-bottom">
          <div className="status-card">
            <div className="status-line">
              <span className="status-dot" />
              <span>Knowledge engine</span>
              <strong>live</strong>
            </div>
            <p>Answers are grounded in the NUST knowledge base.</p>
          </div>
          <button className="rail-settings" type="button" onClick={() => setSettingsOpen(true)}>
            <Icon name="settings" size={16} />
            Connection settings
          </button>
        </div>
      </aside>

      <section className="chat-stage">
        <header className="mobile-header">
          <div className="mobile-brand">narc<span>°</span></div>
          <div className="mobile-actions">
            <button type="button" className="icon-button" onClick={resetSession} aria-label="New chat">
              <Icon name="plus" size={18} />
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={() => setSettingsOpen(true)}
              aria-label="Connection settings"
            >
              <Icon name="settings" size={18} />
            </button>
          </div>
        </header>

        <div className="conversation-topline">
          <div className="topline-left">
            <span className="eyebrow">NUST / ASK ANYTHING</span>
            {sessionId && (
              <span className="memory-chip">
                <span className="memory-pulse" />
                {memoryLabel}
              </span>
            )}
          </div>
          <button className="desktop-settings" type="button" onClick={() => setSettingsOpen(true)}>
            <Icon name="settings" size={15} />
            Configure
          </button>
        </div>

        <main ref={scrollRef} className={`message-panel ${hasMessages ? "has-messages" : "is-empty"}`}>
          {!hasMessages && (
            <section className="hero-state">
              <div className="hero-orbit" aria-hidden="true">
                <div className="orbit-ring ring-one" />
                <div className="orbit-ring ring-two" />
                <div className="orbit-core">
                  <Icon name="spark" size={24} />
                </div>
              </div>

              <div className="hero-copy">
                <span className="hero-kicker">YOUR CAMPUS, IN CONVERSATION</span>
                <h1>
                  Ask NUST.<br />
                  <em>Skip the maze.</em>
                </h1>
                <p>
                  Admissions, programs, policies, offices and campus information — distilled into a
                  focused answer, with the sources behind it.
                </p>
              </div>

              <div className="starter-grid">
                {STARTERS.map((starter, index) => (
                  <button
                    key={starter.prompt}
                    type="button"
                    className="starter-card"
                    onClick={() => sendMessage(starter.prompt)}
                    disabled={loading}
                    style={{ "--delay": `${index * 70}ms` }}
                  >
                    <span className="starter-index">0{index + 1}</span>
                    <div>
                      <span className="starter-eyebrow">{starter.eyebrow}</span>
                      <p>{starter.prompt}</p>
                    </div>
                    <span className="starter-arrow"><Icon name="arrow" size={17} /></span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {messages.map((message, index) => (
            <article
              key={`${message.role}-${index}`}
              className={`message-row ${message.role === "user" ? "user" : "assistant"}`}
            >
              {message.role === "assistant" && (
                <div className="assistant-avatar" aria-hidden="true">
                  <span />
                </div>
              )}

              <div className={`message-wrap ${message.role}`}>
                <div className="message-meta">
                  <span>{message.role === "user" ? "You" : "narc"}</span>
                  {message.role === "assistant" && <span className="answer-state">grounded response</span>}
                </div>

                <div className={`message-bubble ${message.role === "user" ? "user-bubble" : "assistant-bubble"}`}>
                  {message.role === "assistant" ? (
                    <div className="message-content markdown-content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                    </div>
                  ) : (
                    <div className="message-content">{message.content}</div>
                  )}
                </div>

                {message.role === "assistant" && (
                  <div className="assistant-tools">
                    <button type="button" onClick={() => copyAnswer(message.content, index)}>
                      <Icon name={copiedIndex === index ? "check" : "copy"} size={14} />
                      {copiedIndex === index ? "Copied" : "Copy"}
                    </button>

                    {message.sources?.length > 0 && (
                      <button
                        type="button"
                        className={expandedSources[index] ? "is-open" : ""}
                        onClick={() => toggleSources(index)}
                      >
                        <Icon name="link" size={14} />
                        {message.sources.length} source{message.sources.length !== 1 ? "s" : ""}
                        <span className="tool-chevron"><Icon name="chevron" size={14} /></span>
                      </button>
                    )}
                  </div>
                )}

                {message.role === "assistant" && message.sources?.length > 0 && expandedSources[index] && (
                  <div className="source-drawer">
                    <div className="source-drawer-head">
                      <span>Retrieved references</span>
                      <span>{String(message.sources.length).padStart(2, "0")}</span>
                    </div>
                    <div className="source-grid">
                      {message.sources.map((source, sourceIndex) => {
                        const href = source.source_url || source.url;
                        const score = matchLabel(source.rerank_score);
                        const CardTag = href ? "a" : "div";
                        return (
                          <CardTag
                            key={`${source.title || href || "source"}-${sourceIndex}`}
                            className="source-card"
                            {...(href ? { href, target: "_blank", rel: "noreferrer" } : {})}
                          >
                            <div className="source-card-top">
                              <span className="source-number">{String(sourceIndex + 1).padStart(2, "0")}</span>
                              {href && <Icon name="external" size={15} />}
                            </div>
                            <strong>{sourceName(source)}</strong>
                            <div className="source-card-bottom">
                              <span>{source.breadcrumb || "NUST knowledge base"}</span>
                              {score && <em>{score}</em>}
                            </div>
                          </CardTag>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </article>
          ))}

          {loading && (
            <article className="message-row assistant loading-row">
              <div className="assistant-avatar is-thinking" aria-hidden="true">
                <span />
              </div>
              <div className="message-wrap assistant">
                <div className="message-meta">
                  <span>narc</span>
                  <span className="answer-state">searching knowledge</span>
                </div>
                <div className="thinking-card">
                  <span className="thinking-word">Thinking</span>
                  <span className="thinking-dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span className="thinking-line" />
                </div>
              </div>
            </article>
          )}
        </main>

        {error && (
          <div className="error-banner" role="alert">
            <span>Request interrupted</span>
            <p>{error}</p>
            <button type="button" onClick={() => setError(null)} aria-label="Dismiss error">
              <Icon name="close" size={16} />
            </button>
          </div>
        )}

        <footer className="composer-zone">
          <div className="composer-shell">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendMessage();
                }
              }}
              placeholder="Ask about NUST…"
              className="composer-input"
              aria-label="Ask a question"
            />
            <div className="composer-actions">
              <span className="composer-hint">Shift + Enter for a new line</span>
              <button
                type="button"
                onClick={() => sendMessage()}
                disabled={loading || !input.trim()}
                className="send-button"
                aria-label="Send message"
              >
                <span>Send</span>
                <Icon name="arrow" size={18} />
              </button>
            </div>
          </div>
          <div className="composer-footnote">
            <span>AI can misread ambiguous policies. Verify critical decisions with the linked source.</span>
            <span>narc / 01</span>
          </div>
        </footer>
      </section>

      {settingsOpen && (
        <div className="settings-backdrop" role="presentation" onMouseDown={() => setSettingsOpen(false)}>
          <aside className="settings-panel" onMouseDown={(event) => event.stopPropagation()}>
            <div className="settings-heading">
              <div>
                <span className="settings-kicker">DEVELOPER / CONNECTION</span>
                <h2>Chat endpoint</h2>
              </div>
              <button type="button" onClick={() => setSettingsOpen(false)} aria-label="Close settings">
                <Icon name="close" size={18} />
              </button>
            </div>

            <p className="settings-copy">
              Keep this tucked away in production, but it is useful while you move between local,
              staging and hosted backends.
            </p>

            <label className="settings-field">
              <span>Backend base URL</span>
              <input
                type="url"
                value={backendUrl}
                onChange={(event) => setBackendUrl(event.target.value)}
                placeholder="https://api.example.com"
              />
            </label>

            <div className="settings-session">
              <span>Active session</span>
              <code>{sessionId || "No active session yet"}</code>
            </div>

            <div className="settings-actions">
              <button type="button" className="reset-url" onClick={() => setBackendUrl(DEFAULT_BACKEND_URL)}>
                Restore default
              </button>
              <button type="button" className="done-button" onClick={() => setSettingsOpen(false)}>
                Done
              </button>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
