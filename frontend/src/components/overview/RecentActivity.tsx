import { useEffect, useRef } from 'react'
import { LogLine } from '@/components/LogLine'
import type { AuditEntry } from '@/lib/types'

const RECENT_LOG_LINES = 8

/* React's keying already reuses the DOM node for an unchanged row id, so a
   poll that changes nothing causes no flicker and a line under the cursor
   stays put. What React does NOT tell you is which ids weren't there last
   time -- and that is exactly what the arrival flash needs, so the previous
   id set is tracked by hand.

   Everything is new on first paint; flashing all eight would strobe the card
   rather than point at what just happened. */
function useNewIds(entries: AuditEntry[]): Set<number> {
  const seen = useRef<Set<number> | null>(null)
  const fresh = useRef<Set<number>>(new Set())

  const ids = entries.map((e) => e.id)
  const signature = ids.join(',')

  useEffect(() => {
    const current = new Set(ids)
    if (seen.current === null) {
      fresh.current = new Set()
    } else {
      fresh.current = new Set(ids.filter((id) => !seen.current!.has(id)))
    }
    seen.current = current
    // ids is derived from signature; tracking the string keeps this to one
    // run per actual change rather than one per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])

  return fresh.current
}

export function RecentActivity({
  entries,
  onViewAll,
}: {
  entries: AuditEntry[]
  onViewAll: () => void
}) {
  const recent = entries.slice(0, RECENT_LOG_LINES)
  const newIds = useNewIds(recent)

  return (
    <div className="card">
      <div className="card-head">
        <div className="title">Recent activity</div>
      </div>
      <div className="card-body flush">
        {recent.length > 0 ? (
          <div className="logview">
            {recent.map((entry) => (
              <LogLine key={entry.id} entry={entry} isNew={newIds.has(entry.id)} />
            ))}
          </div>
        ) : (
          <div className="empty">
            No pipeline activity yet — open Run and start with Reconcile.
          </div>
        )}
        <button className="view-all" onClick={onViewAll}>
          View full activity log →
        </button>
      </div>
    </div>
  )
}
