import { fmtLogTime, fmtTs, formatDetail } from '@/lib/format'
import { eventPillClass } from '@/lib/labels'
import type { AuditEntry } from '@/lib/types'

/** Severity comes from the same function that colours the Activity log's
    pills, so the stream and the table can never disagree about what counts as
    a failure. */
const LOG_SEV: Record<string, string> = {
  'pill-green': 'sev-ok',
  'pill-red': 'sev-bad',
  'pill-blue': 'sev-info',
  'pill-orange': 'sev-warn',
}

/** Splits "matched=36 split=1" into dimmed keys and inked values, and leaves
    ordinary prose alone. Two thirds of these details are already key=value
    machine output, so the keys recede and the numbers keep the ink. */
function DetailWords({ detail }: { detail: string }) {
  return (
    <>
      {detail.split(/\s+/).map((word, i) => {
        const kv = word.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/)
        return (
          <span key={i}>
            {i > 0 && ' '}
            {kv ? (
              <>
                <span className="k">{kv[1]}=</span>
                <span className="v">{kv[2]}</span>
              </>
            ) : (
              <span className="de">{word}</span>
            )}
          </span>
        )
      })}
    </>
  )
}

/** Exception keys are "cause:kind:REF:-", and the cause is usually already
    spelled out in the event name right beside it -- "partial_payment_updated
    partial_payment:split:SPLIT-002:-" spends half the line saying it twice.
    Drop the leading segment only when the event actually starts with it, so a
    subject whose cause is NOT in the event (action_dispatched on a
    failed_payment key) keeps it. The trailing ":-" is an empty gateway ref and
    never carries anything. */
export function logSubject(e: AuditEntry): string {
  let s = String(e.subject_id || '').replace(/:-$/, '')
  const head = s.split(':')[0]
  if (head && s.includes(':') && e.event.startsWith(head)) s = s.slice(head.length + 1)
  return s
}

export function LogLine({ entry, isNew }: { entry: AuditEntry; isNew: boolean }) {
  const detail = formatDetail(entry.detail)
  const title =
    `${fmtTs(entry.ts, { seconds: true })} · ${entry.actor} · ${entry.event}\n` +
    `${entry.subject_type}:${entry.subject_id}` +
    (detail ? `\n${detail}` : '')

  return (
    <div
      className={['log-line', LOG_SEV[eventPillClass(entry.event)] ?? '', isNew ? 'is-new' : '']
        .filter(Boolean)
        .join(' ')}
      title={title}
    >
      <span className="t">{fmtLogTime(entry.ts)}</span>
      {/* The actor is the emitter, so it sits where a log prefix sits. */}
      <span className="a">{entry.actor}</span>
      <span className="m">
        {/* Raw event name, not the humanised label the table uses: this is the
            string you would grep for, and the backend's own vocabulary. */}
        <span className="ev">{entry.event}</span> <span className="su">{logSubject(entry)}</span>
        {detail && (
          <>
            {'  '}
            <DetailWords detail={detail} />
          </>
        )}
      </span>
    </div>
  )
}
