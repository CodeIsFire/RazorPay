import { describe, expect, it } from 'vitest'
import {
  canRecheck,
  canResolve,
  filterExceptions,
  type ExceptionFilters,
} from './exceptionFilters'
import type { Cause, ExceptionRecord, ExceptionStatus } from './types'

let seq = 0
const rec = (over: Partial<ExceptionRecord> = {}): ExceptionRecord => ({
  id: ++seq,
  exception_key: `key-${seq}`,
  cause: 'failed_payment' as Cause,
  ledger_ref: 'LED-0001',
  gateway_ref: null,
  matched_source: 'gateway',
  amount_paise: 1000,
  detail: null,
  status: 'open' as ExceptionStatus,
  retry_count: 0,
  created_at: '2026-08-20 10:00:00',
  updated_at: '2026-08-20 10:00:00',
  ...over,
})

const base: ExceptionFilters = {
  quickFilter: 'all',
  statusFilter: '',
  causeFilter: '',
  query: '',
  sort: 'desc',
}

describe('the backlog filter', () => {
  // This is the population app/analytics.py counts, and the Insights charts
  // report. A bar reading "7 records" drills through to this filter, so if the
  // two definitions ever diverge the drill-through silently lies about how
  // much work is outstanding.
  const rows = [
    rec({ status: 'open' }),
    rec({ status: 'pending' }),
    rec({ status: 'resolved' }),
    rec({ status: 'abandoned' }),
  ]

  it('is every non-terminal record: open plus pending', () => {
    const got = filterExceptions(rows, { ...base, quickFilter: 'backlog' })
    expect(got.map((r) => r.status).sort()).toEqual(['open', 'pending'])
  })

  it('excludes both terminal states', () => {
    const got = filterExceptions(rows, { ...base, quickFilter: 'backlog' })
    expect(got.some((r) => r.status === 'resolved' || r.status === 'abandoned')).toBe(false)
  })

  it('is wider than needs-action, which is open only', () => {
    const backlog = filterExceptions(rows, { ...base, quickFilter: 'backlog' })
    const needsAction = filterExceptions(rows, { ...base, quickFilter: 'needs_action' })
    expect(backlog.length).toBeGreaterThan(needsAction.length)
  })

  it('still yields to an explicit status select', () => {
    // The select-wins rule has to hold for the new chip too.
    const got = filterExceptions(rows, {
      ...base,
      quickFilter: 'backlog',
      statusFilter: 'resolved',
    })
    expect(got.map((r) => r.status)).toEqual(['resolved'])
  })
})

describe('quick filters', () => {
  const rows = [
    rec({ status: 'open' }),
    rec({ status: 'pending' }),
    rec({ status: 'resolved' }),
    rec({ status: 'abandoned' }),
  ]

  it('maps each chip to the status it stands for', () => {
    expect(filterExceptions(rows, { ...base, quickFilter: 'needs_action' })).toHaveLength(1)
    expect(filterExceptions(rows, { ...base, quickFilter: 'pending' })).toHaveLength(1)
    expect(filterExceptions(rows, { ...base, quickFilter: 'resolved' })).toHaveLength(1)
    expect(filterExceptions(rows, { ...base, quickFilter: 'all' })).toHaveLength(4)
  })

  it('lets the status select win over the chips when both are set', () => {
    // The two controls answer the same question. Intersecting them would let
    // a stale chip silently empty the table after picking a status.
    const out = filterExceptions(rows, {
      ...base,
      quickFilter: 'needs_action',
      statusFilter: 'abandoned',
    })
    expect(out).toHaveLength(1)
    expect(out[0].status).toBe('abandoned')
  })
})

describe('sorting by column', () => {
  const rows = [
    rec({ amount_paise: 900, retry_count: 2, cause: 'duplicate', updated_at: '2026-08-20 10:00:00' }),
    rec({ amount_paise: 100000, retry_count: 0, cause: 'chargeback', updated_at: '2026-08-22 10:00:00' }),
    rec({ amount_paise: 5000, retry_count: 1, cause: 'fee_mismatch', updated_at: '2026-08-21 10:00:00' }),
  ]

  it('orders money numerically, not as text', () => {
    // localeCompare would put 900 after 100000 -- the exact bug that makes a
    // "largest first" sort on an amounts column useless.
    const got = filterExceptions(rows, { ...base, sortKey: 'amount_paise', sort: 'desc' })
    expect(got.map((r) => r.amount_paise)).toEqual([100000, 5000, 900])
  })

  it('orders retry counts numerically too', () => {
    const got = filterExceptions(rows, { ...base, sortKey: 'retry_count', sort: 'asc' })
    expect(got.map((r) => r.retry_count)).toEqual([0, 1, 2])
  })

  it('still defaults to newest update first when no column is given', () => {
    const got = filterExceptions(rows, { ...base, sort: 'desc' })
    expect(got.map((r) => r.updated_at)).toEqual([
      '2026-08-22 10:00:00',
      '2026-08-21 10:00:00',
      '2026-08-20 10:00:00',
    ])
  })

  it('breaks ties on updated_at so equal values do not shuffle between polls', () => {
    const tied = [
      rec({ cause: 'duplicate', amount_paise: 100, updated_at: '2026-08-20 10:00:00' }),
      rec({ cause: 'duplicate', amount_paise: 100, updated_at: '2026-08-24 10:00:00' }),
      rec({ cause: 'duplicate', amount_paise: 100, updated_at: '2026-08-22 10:00:00' }),
    ]
    const got = filterExceptions(tied, { ...base, sortKey: 'cause', sort: 'desc' })
    expect(got.map((r) => r.updated_at)).toEqual([
      '2026-08-24 10:00:00',
      '2026-08-22 10:00:00',
      '2026-08-20 10:00:00',
    ])
  })

  it('does not mutate the source array', () => {
    const before = rows.map((r) => r.id)
    filterExceptions(rows, { ...base, sortKey: 'amount_paise', sort: 'asc' })
    expect(rows.map((r) => r.id)).toEqual(before)
  })
})

describe('search', () => {
  const rows = [
    rec({ cause: 'failed_payment', status: 'open', ledger_ref: 'LED-0056' }),
    rec({ cause: 'chargeback', status: 'resolved', ledger_ref: 'LED-0009' }),
  ]

  it('finds a row by the label shown on screen', () => {
    expect(filterExceptions(rows, { ...base, query: 'Payout Failed' })).toHaveLength(1)
  })

  it('also finds it by the raw value the API uses', () => {
    // Someone reading the API alongside the dashboard searches failed_payment.
    expect(filterExceptions(rows, { ...base, query: 'failed_payment' })).toHaveLength(1)
  })

  it('matches a status label as well as a cause', () => {
    expect(filterExceptions(rows, { ...base, query: 'Processed' })).toHaveLength(1)
  })

  it('searches references', () => {
    expect(filterExceptions(rows, { ...base, query: 'LED-0056' })).toHaveLength(1)
  })

  it('ignores case and surrounding whitespace', () => {
    expect(filterExceptions(rows, { ...base, query: '  CHARGEBACK  ' })).toHaveLength(1)
  })
})

describe('sorting', () => {
  const older = rec({ updated_at: '2026-08-01 09:00:00' })
  const newer = rec({ updated_at: '2026-08-20 09:00:00' })
  const rows = [older, newer]

  it('puts the latest update first by default', () => {
    expect(filterExceptions(rows, base)[0].updated_at).toBe(newer.updated_at)
  })

  it('reverses on request', () => {
    expect(filterExceptions(rows, { ...base, sort: 'asc' })[0].updated_at).toBe(older.updated_at)
  })

  it('does not mutate the array it was given', () => {
    const input = [older, newer]
    filterExceptions(input, base)
    expect(input[0]).toBe(older)
  })
})

describe('row actions', () => {
  it('offers Retry only for a pending timing_lag', () => {
    expect(canRecheck(rec({ status: 'pending', cause: 'timing_lag' }))).toBe(true)
  })

  it('withholds Retry from a pending partial_payment', () => {
    // recheck_exception() rejects anything but timing_lag -- closing out a
    // partial payment is a judgment call, so only "Mark processed" applies.
    expect(canRecheck(rec({ status: 'pending', cause: 'partial_payment' }))).toBe(false)
  })

  it('offers Mark processed until the row is already resolved', () => {
    expect(canResolve(rec({ status: 'open' }))).toBe(true)
    expect(canResolve(rec({ status: 'abandoned' }))).toBe(true)
    expect(canResolve(rec({ status: 'resolved' }))).toBe(false)
  })
})
