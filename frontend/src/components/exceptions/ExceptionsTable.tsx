import { ConflictDetail } from '@/components/exceptions/ConflictDetail'
import { fmtPaise, fmtTs, formatDetail } from '@/lib/format'
import { canRecheck, canResolve } from '@/lib/exceptionFilters'
import { causeLabel, statusLabel, statusPillClass } from '@/lib/labels'
import type { ExceptionRecord } from '@/lib/types'

const COLUMN_COUNT = 9

export function ExceptionsTable({
  rows,
  expandedKey,
  onToggleExpand,
  onResolve,
  onRecheck,
  busyKey,
  emptyMessage,
}: {
  rows: ExceptionRecord[]
  expandedKey: string | null
  onToggleExpand: (key: string) => void
  onResolve: (key: string) => void
  onRecheck: (key: string) => void
  busyKey: string | null
  emptyMessage: string
}) {
  if (!rows.length) return <div className="empty">{emptyMessage}</div>

  return (
    // The exception/audit tables scroll on their own; [data-lenis-prevent]
    // keeps wheel events inside them instead of letting the smoothed page
    // steal the scroll.
    <div className="table-scroll" data-lenis-prevent>
      <table>
        <thead>
          <tr>
            <th>Cause</th>
            <th>Ledger Ref</th>
            <th>UTR / Payout ID</th>
            <th style={{ textAlign: 'right' }}>Amount</th>
            <th>Status</th>
            <th>Retries</th>
            <th>Detail</th>
            <th>Updated</th>
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        {/* The id is load-bearing, not decoration: index.css scopes the row
            cursor and the expanded-row highlight to #exceptions-body, so
            without it clickable rows have no affordance at all. */}
        <tbody id="exceptions-body">
          {rows.map((e) => {
            const expanded = e.exception_key === expandedKey
            const detail = formatDetail(e.detail)
            const busy = busyKey === e.exception_key
            return [
              <tr
                key={e.exception_key}
                className={`exception-row${expanded ? ' expanded' : ''}`}
                onClick={() => onToggleExpand(e.exception_key)}
              >
                {/* The row itself stays clickable for the mouse, but the
                    keyboard needs a real control: a <tr onClick> is reachable
                    by no key at all, which put conflict detail -- a whole
                    feature -- out of reach. stopPropagation so activating the
                    button doesn't also fire the row handler and cancel it. */}
                <td className="cause">
                  <button
                    type="button"
                    className="cause-toggle"
                    aria-expanded={expanded}
                    aria-controls={`conflict-${e.exception_key}`}
                    onClick={(ev) => {
                      ev.stopPropagation()
                      onToggleExpand(e.exception_key)
                    }}
                  >
                    {causeLabel(e.cause)}
                  </button>
                </td>
                <td className="ref" title={e.ledger_ref ?? ''}>
                  {e.ledger_ref || '–'}
                </td>
                <td className="ref" title={e.gateway_ref ?? ''}>
                  {e.gateway_ref || '–'}
                </td>
                <td className="amount">{fmtPaise(e.amount_paise)}</td>
                <td>
                  <span className={`pill ${statusPillClass(e.status)}`}>
                    {statusLabel(e.status)}
                  </span>
                </td>
                <td className="num">{e.retry_count}</td>
                <td className="detail" title={detail}>
                  <span className="clamp">{detail}</span>
                </td>
                <td className="ts">{fmtTs(e.updated_at)}</td>
                <td className="row-actions">
                  <div className="wrap">
                    {/* stopPropagation so acting on a row doesn't also
                        expand it -- the whole row is the expand target. */}
                    {canRecheck(e) && (
                      <button
                        className="btn"
                        title="Reaffirm the transaction and mark it processed"
                        disabled={busy}
                        onClick={(ev) => {
                          ev.stopPropagation()
                          onRecheck(e.exception_key)
                        }}
                      >
                        Retry
                      </button>
                    )}
                    {canResolve(e) && (
                      <button
                        className="btn"
                        disabled={busy}
                        onClick={(ev) => {
                          ev.stopPropagation()
                          onResolve(e.exception_key)
                        }}
                      >
                        Mark processed
                      </button>
                    )}
                  </div>
                </td>
              </tr>,
              expanded && (
                <tr key={`${e.exception_key}:detail`} className="conflict-row">
                  <td colSpan={COLUMN_COUNT}>
                    <div className="conflict-detail" id={`conflict-${e.exception_key}`}>
                      <ConflictDetail exception={e} />
                    </div>
                  </td>
                </tr>
              ),
            ]
          })}
        </tbody>
      </table>
    </div>
  )
}
