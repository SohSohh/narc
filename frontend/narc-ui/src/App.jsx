import { lazy, Suspense, useEffect, useRef, useState } from "react";
import "./App.css";

const Markdown = lazy(() => import("./Markdown.jsx"));

const BACKEND_URL = (import.meta.env.VITE_API_URL ||
  "https://rag-backend.orangeglacier-b4beb8b5.uaenorth.azurecontainerapps.io").replace(/\/$/, "");

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

function safeSourceUrl(raw) {
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}

function sourceName(source) {
  if (typeof source?.title === "string") return source.title;
  const raw = source?.source_url || source?.url;
  if (!raw) return "NUST source";
  try {
    return new URL(raw).hostname.replace(/^www\./, "");
  } catch {
    return typeof raw === "string" ? raw : "NUST source";
  }
}



export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expandedSources, setExpandedSources] = useState({});
  const [copiedIndex, setCopiedIndex] = useState(null);
  const [failedMessage, setFailedMessage] = useState(null);
  const [notice, setNotice] = useState("");
  const requestRef = useRef(null);
  const copyTimerRef = useRef(null);
  const followScrollRef = useRef(true);
  const scrollRef = useRef(null);
  const textareaRef = useRef(null);

  const hasMessages = messages.length > 0;


  useEffect(() => () => {
    requestRef.current?.abort();
    window.clearTimeout(copyTimerRef.current);
  }, []);

  useEffect(() => {
    if (followScrollRef.current) scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    });
  }, [messages, loading]);

  useEffect(() => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "0px";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 156)}px`;
  }, [input]);

  async function sendMessage(overrideText, retry = false) {
    const text = (typeof overrideText === "string" ? overrideText : input).trim();
    if (!text || requestRef.current || text.length > 8000) return;
    const controller = new AbortController();
    requestRef.current = controller;
    const timeout = window.setTimeout(() => controller.abort("timeout"), 60000);
    followScrollRef.current = true;
    setError(null);
    setFailedMessage(null);
    if (!retry) setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);
    try {
      const response = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      if (!response.ok) throw new Error(response.status === 429
        ? "Too many requests. Please wait a moment and try again."
        : "We couldn?t get an answer right now. Please try again.");
      const payload = await response.json();
      if (typeof payload?.answer !== "string" || !payload.answer.trim()) {
        throw new Error("The answer was incomplete. Please try again.");
      }
      if (requestRef.current !== controller) return;
      setNotice("Answer ready.");
      setSessionId(typeof payload.session_id === "string" ? payload.session_id : sessionId);
      setMessages((prev) => [...prev, {
        role: "assistant", content: payload.answer,
        sources: Array.isArray(payload.sources) ? payload.sources.filter((source) => source && typeof source === "object") : [],
      }]);
    } catch (event) {
      if (requestRef.current !== controller) return;
      setFailedMessage(text);
      setError(controller.signal.reason === "timeout"
        ? "This is taking longer than expected. Please try again."
        : event instanceof TypeError ? "Couldn?t connect. Check your connection and try again."
        : event?.message || "Something went wrong. Please try again.");
    } finally {
      window.clearTimeout(timeout);
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }

  function resetSession() {
    const oldSession = sessionId;
    requestRef.current?.abort();
    requestRef.current = null;
    window.clearTimeout(copyTimerRef.current);
    setLoading(false);
    setSessionId(null);
    setMessages([]);
    setInput("");
    setError(null);
    setFailedMessage(null);
    setExpandedSources({});
    setCopiedIndex(null);
    setNotice("");
    textareaRef.current?.focus();
    if (oldSession) {
      fetch(`${BACKEND_URL}/chat/${encodeURIComponent(oldSession)}`, {
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
      setNotice("Answer copied.");
      window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = window.setTimeout(() => setCopiedIndex(null), 1800);
    } catch {
      setNotice("Couldn?t copy. Select the answer to copy it manually.");
    }
  }

  return (
    <div className="site-frame">
      <a className="skip-link" href="#question">Skip to question</a>
      <div className="sr-only" role="status">{loading ? "Searching NUST sources." : notice}</div>

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

        <div className="rail-note"><span className="rail-kicker">A little less searching.</span><p>A little more<br />clarity.</p></div>

        <div className="rail-bottom">
          <div className="status-card">
            <div className="status-line">
              <span className="status-dot" />
              <span>Made for NUST</span>

            </div>
            <p>Answers are grounded in the NUST knowledge base.</p>
          </div>

        </div>
      </aside>

      <section className="chat-stage">
        <header className="mobile-header">
          <div className="mobile-brand">narc<span>°</span></div>
          <div className="mobile-actions">
            <button type="button" className="icon-button" onClick={resetSession} aria-label="New chat">
              <Icon name="plus" size={18} />
            </button>

          </div>
        </header>

        <div className="conversation-topline">
          <div className="topline-left">
            <span className="eyebrow">NUST / ASK ANYTHING</span>

          </div>
          <span className="topline-caption">Your campus companion</span>
        </div>

        <main aria-label="Conversation" onScroll={() => { const panel = scrollRef.current; followScrollRef.current = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 100; }} ref={scrollRef} className={`message-panel ${hasMessages ? "has-messages" : "is-empty"}`}>
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
                <span className="hero-kicker">WELCOME TO NARC</span>
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

                </div>

                <div className={`message-bubble ${message.role === "user" ? "user-bubble" : "assistant-bubble"}`}>
                  {message.role === "assistant" ? (
                    <div className="message-content markdown-content">
                      <Suspense fallback={<div className="plain-answer">{message.content}</div>}><Markdown>{message.content}</Markdown></Suspense>
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
                        aria-expanded={Boolean(expandedSources[index])}
                        aria-controls={`sources-${index}`}
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
                  <div className="source-drawer" id={`sources-${index}`}>
                    <div className="source-drawer-head">
                      <span>Retrieved references</span>
                      <span>{String(message.sources.length).padStart(2, "0")}</span>
                    </div>
                    <div className="source-grid">
                      {message.sources.map((source, sourceIndex) => {
                        const href = safeSourceUrl(source.source_url || source.url);
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
                              <span>{typeof source.breadcrumb === "string" ? source.breadcrumb : "NUST knowledge base"}</span>
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
            {failedMessage && <button className="retry-button" type="button" disabled={loading} onClick={() => sendMessage(failedMessage, true)}>Try again</button>}
            <button type="button" onClick={() => setError(null)} aria-label="Dismiss error">
              <Icon name="close" size={16} />
            </button>
          </div>
        )}

        <footer className="composer-zone">
          <div className="composer-shell">
            <textarea
              id="question"
              maxLength={8000}
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
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

          </div>
        </footer>
      </section>

    </div>
  );
}
