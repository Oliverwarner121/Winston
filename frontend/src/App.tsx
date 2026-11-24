import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import LiquidGlass from 'liquid-glass-react'
import './App.css'

type Message = {
  id: number
  role: 'assistant' | 'user'
  content: string
  isError?: boolean
  confirmationId?: string
  proposalId?: string
  proposalSummary?: string
  proposalDiff?: string
  proposalFiles?: string[]
  proposalStatus?: string
  testOutput?: string
  testsPassed?: boolean
}

type ChatResponse = {
  response: string
  confirmation_id?: string
  confirmation_message?: string
  proposal_id?: string
  proposal_summary?: string
  proposal_diff?: string
  proposal_files?: string[]
  proposal_status?: string
  test_output?: string
  tests_passed?: boolean
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
          assistantMessageFromResponse(result, nextId.current++),
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

  async function handleConfirmation(confirmationId: string, approved: boolean) {
    if (isSending) return
    setIsSending(true)

    try {
      const response = await fetch(
        `/chat/confirmations/${encodeURIComponent(confirmationId)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approved }),
        },
      )
      if (!response.ok) {
        let errorMessage = `Confirmation request failed (${response.status}).`
        try {
          const error = (await response.json()) as { detail?: string }
          if (error.detail) errorMessage = error.detail
        } catch {
          // Keep the status-based message if the server response is not JSON.
        }
        throw new Error(errorMessage)
      }

      const result = (await response.json()) as ChatResponse
      setMessages((current) => [
        ...current.map((message) =>
          message.confirmationId === confirmationId
            ? { ...message, confirmationId: undefined }
            : message,
        ),
        {
          id: nextId.current++,
          role: 'assistant',
          content: result.confirmation_message ?? result.response,
          confirmationId: result.confirmation_id,
        },
      ])
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: 'assistant',
          content:
            error instanceof Error
              ? error.message
              : 'Unable to send the confirmation response.',
          isError: true,
        },
      ])
    } finally {
      setIsSending(false)
    }
  }

  async function handleCodeProposal(
    proposalId: string,
    action: 'apply' | 'discard' | 'tests',
  ) {
    if (isSending) return
    setIsSending(true)

    try {
      const response = await fetch(`/chat/code-proposals/${proposalId}/${action}`, {
        method: 'POST',
      })
      if (!response.ok) {
        let errorMessage = `Code proposal request failed (${response.status}).`
        try {
          const error = (await response.json()) as { detail?: string }
          if (error.detail) errorMessage = error.detail
        } catch {
          // Keep the status-based message if the server response is not JSON.
        }
        throw new Error(errorMessage)
      }

      const result = (await response.json()) as ChatResponse
      setMessages((current) =>
        current.map((message) =>
          message.proposalId === proposalId
            ? {
                ...message,
                content: result.response,
                proposalId: result.proposal_id,
                proposalSummary: result.proposal_summary,
                proposalDiff: result.proposal_diff,
                proposalFiles: result.proposal_files,
                proposalStatus: result.proposal_status,
                testOutput: result.test_output,
                testsPassed: result.tests_passed,
              }
            : message,
        ),
      )
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: 'assistant',
          content:
            error instanceof Error
              ? error.message
              : 'Unable to process the code proposal.',
          isError: true,
        },
      ])
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
            <img
              alt=""
              className="winston-mark-image"
              src="/winston-avatar.png"
            />
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
              <img
                alt=""
                className="winston-mark-image"
                src="/winston-avatar.png"
              />
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
              <p>YOUR PERSONAL ASSISTANT</p>
            </div>

            <div className="message-list">
              {messages.map((message) => (
                <article
                  className={`message-row ${message.role} ${message.isError ? 'error' : ''}`}
                  key={message.id}
                >
                  {message.role === 'assistant' && (
                    <span className="message-avatar" aria-hidden="true">
                      <img
                        alt=""
                        className="message-avatar-image"
                        src="/winston-avatar.png"
                      />
                    </span>
                  )}
                  <div className="message-content">
                    <span className="message-author">
                      {message.role === 'assistant' ? 'Winston' : 'You'}
                    </span>
                    <p>{message.content}</p>
                    {message.confirmationId && (
                      <div className="confirmation-actions">
                        <button
                          className="confirmation-button approve"
                          disabled={isSending}
                          onClick={() => {
                            if (message.confirmationId) {
                              void handleConfirmation(message.confirmationId, true)
                            }
                          }}
                          type="button"
                        >
                          Approve
                        </button>
                        <button
                          className="confirmation-button"
                          disabled={isSending}
                          onClick={() => {
                            if (message.confirmationId) {
                              void handleConfirmation(message.confirmationId, false)
                            }
                          }}
                          type="button"
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                    {message.proposalId && (
                      <div className="code-proposal">
                        {message.proposalSummary && (
                          <h3>{message.proposalSummary}</h3>
                        )}
                        {message.proposalFiles && (
                          <p className="proposal-files">
                            Files: {message.proposalFiles.join(', ')}
                          </p>
                        )}
                        {message.proposalDiff && (
                          <details className="proposal-diff" open>
                            <summary>Review proposed diff</summary>
                            <pre>{message.proposalDiff}</pre>
                          </details>
                        )}
                        {message.proposalStatus === 'pending' && (
                          <div className="confirmation-actions">
                            <button
                              className="confirmation-button approve"
                              disabled={isSending}
                              onClick={() => {
                                if (message.proposalId) {
                                  void handleCodeProposal(message.proposalId, 'apply')
                                }
                              }}
                              type="button"
                            >
                              Apply patch
                            </button>
                            <button
                              className="confirmation-button"
                              disabled={isSending}
                              onClick={() => {
                                if (message.proposalId) {
                                  void handleCodeProposal(message.proposalId, 'discard')
                                }
                              }}
                              type="button"
                            >
                              Discard
                            </button>
                          </div>
                        )}
                        {message.proposalStatus === 'applied' && (
                          <>
                            <p className="test-approval-warning">
                              Running tests executes repository code as your user.
                            </p>
                            <div className="confirmation-actions">
                              <button
                                className="confirmation-button approve"
                                disabled={isSending}
                                onClick={() => {
                                  if (message.proposalId) {
                                    void handleCodeProposal(message.proposalId, 'tests')
                                  }
                                }}
                                type="button"
                              >
                                Run backend tests
                              </button>
                            </div>
                          </>
                        )}
                        {message.proposalStatus === 'tested' && (
                          <>
                            <p className={message.testsPassed ? 'tests-passed' : 'tests-failed'}>
                              {message.testsPassed
                                ? 'Backend tests passed.'
                                : 'Backend tests did not pass.'}
                            </p>
                            {message.testOutput && (
                              <pre className="test-output">{message.testOutput}</pre>
                            )}
                          </>
                        )}
                      </div>
                    )}
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
                    <img
                      alt=""
                      className="message-avatar-image"
                      src="/winston-avatar.png"
                    />
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

function assistantMessageFromResponse(
  response: ChatResponse,
  id: number,
): Message {
  return {
    id,
    role: 'assistant',
    content: response.confirmation_message ?? response.response,
    confirmationId: response.confirmation_id,
    proposalId: response.proposal_id,
    proposalSummary: response.proposal_summary,
    proposalDiff: response.proposal_diff,
    proposalFiles: response.proposal_files,
    proposalStatus: response.proposal_status,
    testOutput: response.test_output,
    testsPassed: response.tests_passed,
  }
}

export default App
