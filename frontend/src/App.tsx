import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import LiquidGlass from 'liquid-glass-react'
import './App.css'

type Message = {
  id: number
  role: 'assistant' | 'user'
  content: string
  isError?: boolean
}

type ChatResponse = {
  response: string
}

const welcomeMessage: Message = {
  id: 0,
  role: 'assistant',
  content:
    "Hi, I'm Winston. I'm ready to help with development, homelab management, and staying organised. What would you like to work on?",
}

function App() {
  const [messages, setMessages] = useState<Message[]>([welcomeMessage])
  const [draft, setDraft] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isBackendOnline, setIsBackendOnline] = useState<boolean | null>(null)
  const nextId = useRef(1)
  const messageListRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const controller = new AbortController()

    fetch('/health', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error('Backend health check failed')
        }
        setIsBackendOnline(true)
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setIsBackendOnline(false)
        }
      })

    return () => controller.abort()
  }, [])

  useEffect(() => {
    messageListRef.current?.scrollTo({
      top: messageListRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages, isSending])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = draft.trim()
    if (!message || isSending) return

    setMessages((current) => [
      ...current,
      { id: nextId.current++, role: 'user', content: message },
    ])
    setDraft('')
    setIsSending(true)

    try {
      let response: Response
      try {
        response = await fetch('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message }),
        })
      } catch {
        setIsBackendOnline(false)
        setMessages((current) => [
          ...current,
          {
            id: nextId.current++,
            role: 'assistant',
            content:
              "I couldn't reach the backend. Check that the Winston API is running, then try again.",
            isError: true,
          },
        ])
        return
      }

      setIsBackendOnline(true)
      if (!response.ok) {
        setMessages((current) => [
          ...current,
          {
            id: nextId.current++,
            role: 'assistant',
            content: `The backend returned an error (${response.status}). Please try again.`,
            isError: true,
          },
        ])
        return
      }

      try {
        const result = (await response.json()) as ChatResponse
        setMessages((current) => [
          ...current,
          {
            id: nextId.current++,
            role: 'assistant',
            content: result.response,
          },
        ])
      } catch {
        setMessages((current) => [
          ...current,
          {
            id: nextId.current++,
            role: 'assistant',
            content: 'The backend returned an unreadable response. Please try again.',
            isError: true,
          },
        ])
      }
    } finally {
      setIsSending(false)
    }
  }

  function startNewChat() {
    setMessages([welcomeMessage])
    setDraft('')
  }

  const connectionLabel =
    isBackendOnline === null
      ? 'Checking backend'
      : isBackendOnline
        ? 'Backend connected'
        : 'Backend offline'

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Winston home">
          <span className="brand-mark" aria-hidden="true">
            W
          </span>
          <span>Winston</span>
        </a>

        <button className="new-chat-button" onClick={startNewChat} type="button">
          <span aria-hidden="true">+</span>
          New chat
        </button>

        <div className="sidebar-section">
          <p className="sidebar-label">Workspace</p>
          <div className="workspace-item" aria-current="page">
            <span className="conversation-icon" aria-hidden="true">
              ◌
            </span>
            Getting started
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="avatar">O</div>
          <div className="profile-copy">
            <span className="profile-name">Local workspace</span>
            <span className="profile-plan">Private by design</span>
          </div>
          <span className="privacy-dot" aria-label="Local workspace" />
        </div>
      </aside>

      <main className="chat-panel">
        <header className="topbar">
          <div className="mobile-brand">
            <span className="brand-mark" aria-hidden="true">
              W
            </span>
            Winston
          </div>
          <div className="conversation-title">Getting started</div>
          <div
            className={`connection-status ${isBackendOnline === false ? 'offline' : ''}`}
            role="status"
          >
            <span className="status-dot" />
            {connectionLabel}
          </div>
        </header>

        <section className="conversation" aria-label="Chat conversation">
          <div className="messages" ref={messageListRef} aria-live="polite">
            <div className="conversation-intro">
              <span className="intro-mark" aria-hidden="true">
                W
              </span>
              <p>YOUR PERSONAL ASSISTANT</p>
              <h1>A little more focus.</h1>
              <p className="intro-description">
                Your private space to think, plan, and get things done.
              </p>
            </div>

            <div className="message-list">
              {messages.map((message) => (
                <article
                  className={`message-row ${message.role} ${message.isError ? 'error' : ''}`}
                  key={message.id}
                >
                  {message.role === 'assistant' && (
                    <span className="message-avatar" aria-hidden="true">
                      W
                    </span>
                  )}
                  <div className="message-content">
                    <span className="message-author">
                      {message.role === 'assistant' ? 'Winston' : 'You'}
                    </span>
                    <p>{message.content}</p>
                  </div>
                  {message.role === 'user' && (
                    <span className="user-avatar" aria-hidden="true">
                      O
                    </span>
                  )}
                </article>
              ))}
              {isSending && (
                <div className="message-row assistant" role="status">
                  <span className="message-avatar" aria-hidden="true">
                    W
                  </span>
                  <div className="message-content">
                    <span className="message-author">Winston</span>
                    <p className="thinking">Connecting to the backend…</p>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="composer-wrap">
            <div className="composer-glass-host">
              <LiquidGlass
                aberrationIntensity={0.35}
                blurAmount={0.2}
                className="composer-liquid-glass"
                cornerRadius={14}
                displacementScale={24}
                elasticity={0.08}
                padding="0"
                saturation={145}
                style={{
                  position: 'absolute',
                  top: '50%',
                  left: '50%',
                  width: '100%',
                  zIndex: 1,
                }}
              >
                <form className="composer" onSubmit={handleSubmit}>
                  <label className="visually-hidden" htmlFor="message-input">
                    Message Winston
                  </label>
                  <textarea
                    id="message-input"
                    maxLength={4000}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault()
                        event.currentTarget.form?.requestSubmit()
                      }
                    }}
                    placeholder="Message Winston…"
                    rows={1}
                    value={draft}
                  />
                  <button
                    aria-label="Send message"
                    className="send-button"
                    disabled={!draft.trim() || isSending}
                    type="submit"
                  >
                    <svg
                      aria-hidden="true"
                      fill="none"
                      height="18"
                      viewBox="0 0 20 20"
                      width="18"
                    >
                      <path
                        d="M10 15.833V4.167M4.167 10 10 4.167 15.833 10"
                        stroke="currentColor"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth="1.7"
                      />
                    </svg>
                  </button>
                </form>
              </LiquidGlass>
            </div>
            <p className="composer-hint">
              <span>Enter to send</span>
              <span>·</span>
              <span>Shift + Enter for a new line</span>
              <span className="demo-hint">Powered by your local Ollama model</span>
            </p>
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
