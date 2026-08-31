import { causeLabel, statusLabel } from './labels'
import type { ExceptionRecord } from './types'

export type QuickFilter = 'all' | 'backlog' | 'needs_action' | 'pending' | 'resolved'
export type SortOrder = 'asc' | 'desc'

/** Columns an operator can order the table by. Deliberately a closed set: these
    are the ones with a meaningful ordering, so "Detail" (free text) and the
    action column are not sortable and do not pretend to be. */
export type SortKey = 'updated_at' | 'amount_paise' | 'cause' | 'status' | 'retry_count'

export interface ExceptionFilters {
  quickFilter: QuickFilter
  statusFilter: string
  causeFilter: string
  query: string
  sort: SortOrder
  /** Defaults to updated_at, which is what the table has always ordered by. */
  sortKey?: SortKey
}

export function matchesQuickFilter(e: ExceptionRecord, qf: QuickFilter): boolean {
  /* The backlog is every non-terminal record: open plus pending. This is not a
     convenience grouping -- it is the exact population app/analytics.py counts,
     so the Insights figures ("Records in backlog", "Value at risk", every bar
     in Backlog by cause) describe precisely this set. Drilling from a bar that
     reads "7 records" has to land on those 7 and not on the 14 that include
     already-resolved history. */
  if (qf === 'backlog') return e.status === 'open' || e.status === 'pending'
  if (qf === 'needs_action') return e.status === 'open'
  if (qf === 'pending') return e.status === 'pending'
  if (qf === 'resolved') return e.status === 'resolved'
  return true
}

/** Search runs over the labels the reader can actually see, plus the raw refs:
    searching "Payout Failed" should work, and so should "failed_payment" for
    anyone reading the API alongside. One joined haystack rather than per-field
    matching, so a term spanning two fields still finds the row. */
export function exceptionHaystack(e: ExceptionRecord): string {
  return [
    e.cause,
    causeLabel(e.cause),
    e.status,
    statusLabel(e.status),
    e.ledger_ref,
    e.gateway_ref,
    e.detail,
  ]
    .join(' ')
    .toLowerCase()
}

/** The status select and the quick-filter chips are two views of one choice,
    so the select WINS whenever it is set and the chips are ignored -- picking
    one clears the other in the UI, and this is the same rule at render time. */
export function filterExceptions(
  all: ExceptionRecord[],
  { quickFilter, statusFilter, causeFilter, query, sort, sortKey }: ExceptionFilters,
): ExceptionRecord[] {
  const q = query.trim().toLowerCase()

  const rows = all.filter((e) => {
    if (statusFilter) {
      if (e.status !== statusFilter) return false
    } else if (!matchesQuickFilter(e, quickFilter)) {
      return false
    }
    if (causeFilter && e.cause !== causeFilter) return false
    if (q && !exceptionHaystack(e).includes(q)) return false
    return true
  })

  /* Timestamps are lexicographically ordered in both formats the backend
     emits, so string compare is correct for those and avoids parsing 500 dates
     on every keystroke. Amount and retry count are genuinely numeric and must
     not go through localeCompare, which would order 9 after 1000.

     The tie-break on updated_at is what keeps the sort stable to read: without
     it, every record sharing a cause or a status sits in whatever order the
     API happened to return, and the order shuffles under the 15s poll. */
  const key = sortKey ?? 'updated_at'
  const dir = sort === 'asc' ? 1 : -1

  return rows.slice().sort((a, b) => {
    let cmp: number
    if (key === 'amount_paise' || key === 'retry_count') {
      cmp = a[key] - b[key]
    } else {
      cmp = (a[key] || '').localeCompare(b[key] || '')
    }
    if (cmp === 0 && key !== 'updated_at') {
      cmp = (a.updated_at || '').localeCompare(b.updated_at || '')
    }
    return dir * cmp
  })
}

/** partial_payment also reaches 'pending', but recheck_exception() only
    accepts timing_lag -- closing out a partial payment is a real judgment
    call (see app/router.py), so only "Mark processed" applies there. */
export const canRecheck = (e: ExceptionRecord) =>
  e.status === 'pending' && e.cause === 'timing_lag'

export const canResolve = (e: ExceptionRecord) => e.status !== 'resolved'
