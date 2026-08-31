import { IconRefresh } from '@/components/icons'

/* Shown when a read fails, in place of the data it was meant to show.

   This exists because the alternative is dangerous rather than merely untidy.
   Every tab used to render `data ?? []`, so a dropped request produced the
   same screen as a genuinely clean backlog: "Nothing needs attention", or
   "No pipeline activity yet -- open Run and start with Reconcile". On a tool
   that dispatches payouts, one of those tells an operator nothing is wrong
   while the truth is unknown, and the other invites them to re-run a pipeline
   that moves money because a *read* failed.

   So it must never be mistakable for an empty state: it says the request
   failed, keeps the reason, and offers the retry rather than describing one. */
export function LoadFailed({
  what,
  error,
  onRetry,
  retrying = false,
}: {
  /** What could not be loaded, as a noun phrase: "the exception list". */
  what: string
  error: unknown
  onRetry: () => void
  retrying?: boolean
}) {
  const detail = error instanceof Error ? error.message : null
  return (
    <div className="load-failed" role="alert">
      <div className="lf-head">Couldn’t load {what}.</div>
      {/* The server's own message where there is one -- api.ts passes
          FastAPI's `detail` through verbatim, and it is usually the most
          specific thing anyone will see. */}
      {detail && <div className="lf-detail">{detail}</div>}
      <div className="lf-note">
        The figures below are unavailable — they are not zero.
      </div>
      <button className="btn" onClick={onRetry} disabled={retrying}>
        <IconRefresh />
        {retrying ? 'Retrying…' : 'Try again'}
      </button>
    </div>
  )
}
