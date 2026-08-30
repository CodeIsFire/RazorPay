import { useEffect, useRef } from 'react'
import { fmtPaise } from '@/lib/format'
import type { RoutePreview } from '@/lib/types'

/* The step between "Run route" and money moving.

   Route dispatches real payouts over the RazorpayX Payouts API and nothing it
   does can be undone from this dashboard -- there is no un-resolve endpoint,
   and a dispatched payout is settled by webhook, not by us. It used to fire
   straight from a menu click, telling the operator what happened only in the
   toast afterwards.

   The counts come from GET /pipeline/route/preview, which runs the router's
   own decisions against a throwaway copy of the database, so this shows what
   will actually happen rather than a client-side guess -- the frontend cannot
   see the two inputs that matter most, an exception's age and whether a
   previous attempt is still in flight. */
export function RouteConfirm({
  preview,
  loading,
  error,
  onConfirm,
  onCancel,
  confirming,
}: {
  preview: RoutePreview | null
  loading: boolean
  error: unknown
  onConfirm: () => void
  onCancel: () => void
  confirming: boolean
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)

  // Focus lands on Cancel, not Dispatch: the safe choice should be the one a
  // stray Return key picks.
  useEffect(() => {
    cancelRef.current?.focus()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const nothingToDo = preview !== null && preview.would_dispatch === 0

  return (
    <div className="modal-scrim" onClick={onCancel}>
      <div
        ref={dialogRef}
        className="modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="route-confirm-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-title" id="route-confirm-title">
          Dispatch payouts?
        </div>

        {loading && <div className="modal-body">Checking what this would dispatch…</div>}

        {error != null && !loading && (
          <div className="modal-body">
            {/* Never offer to dispatch against an unknown backlog. */}
            Couldn’t check what this would dispatch
            {error instanceof Error ? `: ${error.message}` : '.'} Try again before running route.
          </div>
        )}

        {preview && !loading && error == null && (
          <div className="modal-body">
            {nothingToDo ? (
              <p>
                Nothing is eligible to dispatch right now. Running route would make no payouts.
              </p>
            ) : (
              <>
                <p>
                  This dispatches <b>{preview.would_dispatch}</b>{' '}
                  payout{preview.would_dispatch === 1 ? '' : 's'} worth{' '}
                  <b>{fmtPaise(preview.value_paise)}</b> over the real RazorpayX Payouts API.
                  It can’t be undone from here.
                </p>
                <ul className="modal-list">
                  <li>
                    <b>{preview.would_skip}</b> held — already in flight, or waiting on
                    independent evidence
                  </li>
                  <li>
                    <b>{preview.would_abandon}</b> abandoned — past the retry or age limit
                  </li>
                </ul>
              </>
            )}
          </div>
        )}

        <div className="modal-actions">
          <button ref={cancelRef} className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn btn-danger"
            onClick={onConfirm}
            disabled={loading || confirming || error != null || nothingToDo}
          >
            {confirming
              ? 'Dispatching…'
              : preview && preview.would_dispatch > 0
                ? `Dispatch ${preview.would_dispatch} payout${preview.would_dispatch === 1 ? '' : 's'}`
                : 'Dispatch payouts'}
          </button>
        </div>
      </div>
    </div>
  )
}
