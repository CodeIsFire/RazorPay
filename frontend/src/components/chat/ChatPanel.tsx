import { useEffect, useRef, useState } from 'react'
import { IconClose, IconHelp } from '@/components/icons'
import { useToast } from '@/components/Toast'
import { useDismissableMenu } from '@/hooks/useDismissableMenu'
import { api, ApiError } from '@/lib/api'
import { useIntegrationStatus } from '@/lib/queries'
import type { ChatTurn } from '@/lib/types'

const HELP_BLURB =
  'Reconcile → Recover reconciles a synthetic ledger against RazorpayX test-mode transactions, then recovers what didn’t match over the real Payouts API.'

export function ChatPanel() {
  const { open, setOpen, close, triggerRef, panelRef } = useDismissableMenu<
    HTMLButtonElement,
    HTMLDivElement
  >()
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const toast = useToast()

  const { data: status } = useIntegrationStatus()
  // Boolean only -- the endpoint never publishes the key itself.
  const configured = !!status?.assistant_configured

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [turns, sending])

  async function send(ev: React.FormEvent) {
    ev.preventDefault()
    const q = draft.trim()
    if (!q || sending) return

    setDraft('')
    setSending(true)
    try {
      const { reply } = await api.chat(q, turns)
      setTurns((prev) => [...prev, { role: 'user', content: q }, { role: 'assistant', content: reply }])
    } catch (err) {
      // Errors go to the toast, matching how the rest of the app reports
      // failure -- and leaving no half-finished exchange in the transcript.
      toast(err instanceof ApiError ? err.message : String(err), true)
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  return (
    <>
      {open && (
        <div
          ref={panelRef}
          className="chat-panel"
          id="chat-panel"
          role="dialog"
          aria-label="Ask about this dashboard"
        >
          <div className="chat-head">
            <div>
              <div className="chat-title">Ask about your reconciliation</div>
              <div className="chat-sub">
                Reads your live totals and open exceptions. Ask about a reference like SPLIT-002.
              </div>
            </div>
            <button
              className="btn icon-btn"
              title="Close"
              aria-label="Close"
              onClick={() => close()}
            >
              <IconClose />
            </button>
          </div>

          <div className="chat-log" role="log" aria-live="polite" data-lenis-prevent ref={logRef}>
            {turns.length === 0 && !sending ? (
              <div className="chat-empty">
                Ask about what the pipeline found — <b>why is my match rate low</b>, what's wrong
                with a specific reference, or what to work on first.
              </div>
            ) : (
              turns.map((t, i) => (
                // Replies are model output and render as plain text by
                // construction -- React escapes it, which is the same
                // guarantee the original got from textContent. Never
                // dangerouslySetInnerHTML here.
                <div key={i} className={t.role === 'user' ? 'chat-msg me' : 'chat-msg bot'}>
                  {t.content}
                </div>
              ))
            )}
            {sending && <div className="chat-thinking">Thinking…</div>}
          </div>

          <form className="chat-form" onSubmit={send}>
            <input
              ref={inputRef}
              type="text"
              autoComplete="off"
              maxLength={1000}
              value={draft}
              placeholder="Why is my match rate low?"
              aria-label="Your question"
              onChange={(e) => setDraft(e.target.value)}
            />
            <button className="btn" id="btn-chat-send" type="submit" disabled={sending}>
              Ask
            </button>
          </form>
        </div>
      )}

      <button
        ref={triggerRef}
        className="help-fab"
        aria-expanded={open}
        aria-controls="chat-panel"
        title={configured ? 'Ask about your reconciliation data' : 'About this app'}
        onClick={() => {
          // Without a key the assistant cannot answer, so the button keeps
          // doing the one useful thing it did before rather than opening a
          // dead panel.
          if (!configured) {
            toast(HELP_BLURB)
            return
          }
          setOpen(!open)
        }}
      >
        <IconHelp />
        Need help?
      </button>
    </>
  )
}
