import { ConflictDetail } from '@/components/exceptions/ConflictDetail'
import { LoadingAnnounce, TableSkeleton } from '@/components/ui/Skeleton'
import { fmtPaise, fmtTs, formatDetail } from '@/lib/format'
import {
  canRecheck,
  canResolve,
  type SortKey,
  type SortOrder,
} from '@/lib/exceptionFilters'
import { causeLabel, statusLabel, statusPillClass } from '@/lib/labels'
import type { ExceptionRecord } from '@/lib/types'

const COLUMN_COUNT = 9

const SORT_ARIA: Record<SortOrder, 'ascending' | 'descending'> = {
  asc: 'ascending',
  desc: 'descending',
}

/* Sortable columns are buttons inside the th, not click handlers on the th
   itself: a bare clickable cell is invisible to the keyboard and announces
   nothing. aria-sort goes on the th, which is where assistive tech looks for
   it, while the button carries the label and the activation.

   Defined at module scope on purpose. Declared inside the table's render it
   would be a NEW component type on every render, so React would unmount and
   remount the entire header row each time the sort changed -- which threw
   keyboard focus back to <body> the moment anyone pressed Enter on a header,
   making the control unusable by the people who most need it. */
function SortHeader({
  label,
  column,
  className,
  sortKey,
  sort,
  onSort,
}: {
  label: string
  column: SortKey
  className?: string
  sortKey: SortKey
  sort: SortOrder
  onSort?: (key: SortKey) => void
}) {
  // The plain/interactive decision lives here, not at each call site. When it
  // was a ternary repeated per column, a sixth column added without the guard
  // would have rendered a focusable button that looks sortable and does
  // nothing -- the kind of thing that only shows up under a keyboard.
  if (!onSort) return <th className={className}>{label}</th>

  const active = sortKey === column
  return (
    <th className={className} aria-sort={active ? SORT_ARIA[sort] : 'none'}>
      <button
        type="button"
        className={`col-sort${active ? ' active' : ''}`}
        onClick={() => onSort?.(column)}
        // Names the result of pressing, not the current state -- a control
        // should say what it will do.
        title={`Sort by ${label.toLowerCase()}${active && sort === 'asc' ? ', descending' : ', ascending'}`}
      >
        <span>{label}</span>
        <span className="col-sort-arrow" aria-hidden="true">
          {active ? (sort === 'asc' ? '↑' : '↓') : '↕'}
        </span>
      </button>
    </th>
  )
}

export function ExceptionsTable({
  rows,
  expandedKey,
  onToggleExpand,
  onResolve,
  onRecheck,
  busyKey,
  emptyMessage,
  loading = false,
  sortKey = 'updated_at',
  sort = 'desc',
  onSort,
}: {
  rows: ExceptionRecord[]
  expandedKey: string | null
  onToggleExpand: (key: string) => void
  onResolve: (key: string) => void
  onRecheck: (key: string) => void
  busyKey: string | null
  emptyMessage: string
  loading?: boolean
  sortKey?: SortKey
  sort?: SortOrder
  /** Omitted in tests and anywhere sorting is not offered; headers stay plain. */
  onSort?: (key: SortKey) => void
}) {
  const sortProps = { sortKey, sort, onSort }

  const head = (
    <thead>
      <tr>
        <SortHeader label="Cause" column="cause" {...sortProps} />
        <th>Ledger Ref</th>
        <th>UTR / Payout ID</th>
        <SortHeader label="Amount" column="amount_paise" className="amount" {...sortProps} />
        <SortHeader label="Status" column="status" {...sortProps} />
        <SortHeader label="Retries" column="retry_count" {...sortProps} />
        {/* Updated sits before Detail on purpose. The actions column is sticky
            and everything else slides underneath it, so whichever column ends
            up last is the one obscured at rest on a narrow viewport. Detail is
            clamped prose that already truncates and survives losing its tail;
            a timestamp does not degrade, it just disappears. The expendable
            column is the one that should take the hit. */}
        <SortHeader label="Updated" column="updated_at" {...sortProps} />
        <th>Detail</th>
        <th>
          <span className="sr-only">Actions</span>
        </th>
      </tr>
    </thead>
  )

  /* The real header stays mounted while the body is a placeholder, so the
     columns are already sized when the rows arrive and nothing jumps sideways
     on the first paint of real data. */
  if (loading) {
    return (
      <div className="table-scroll" data-lenis-prevent>
        <LoadingAnnounce what="the exception list" />
        <table>
          {head}
          <TableSkeleton columns={COLUMN_COUNT} />
        </table>
      </div>
    )
  }

  if (!rows.length) return <div className="empty">{emptyMessage}</div>

  return (
    // The exception/audit tables scroll on their own; [data-lenis-prevent]
    // keeps wheel events inside them instead of letting the smoothed page
    // steal the scroll.
    <div className="table-scroll" data-lenis-prevent>
      <table>
        {head}
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
                <td className="ts">{fmtTs(e.updated_at)}</td>
                <td className="detail" title={detail}>
                  <span className="clamp">{detail}</span>
                </td>
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
