import { causeLabel, statusLabel } from './labels'
import type { ExceptionRecord } from './types'

export type QuickFilter = 'all' | 'needs_action' | 'pending' | 'resolved'
export type SortOrder = 'asc' | 'desc'

export interface ExceptionFilters {
  quickFilter: QuickFilter
  statusFilter: string
  causeFilter: string
  query: string
  sort: SortOrder
}

export function matchesQuickFilter(e: ExceptionRecord, qf: QuickFilter): boolean {
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
  { quickFilter, statusFilter, causeFilter, query, sort }: ExceptionFilters,
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

  // Timestamps are lexicographically ordered in both formats the backend
  // emits, so string compare is a correct sort here and avoids parsing 500
  // dates on every keystroke.
  return rows.slice().sort((a, b) => {
    const cmp = (a.updated_at || '').localeCompare(b.updated_at || '')
    return sort === 'asc' ? cmp : -cmp
  })
}

/** partial_payment also reaches 'pending', but recheck_exception() only
    accepts timing_lag -- closing out a partial payment is a real judgment
    call (see app/router.py), so only "Mark processed" applies there. */
export const canRecheck = (e: ExceptionRecord) =>
  e.status === 'pending' && e.cause === 'timing_lag'

export const canResolve = (e: ExceptionRecord) => e.status !== 'resolved'
