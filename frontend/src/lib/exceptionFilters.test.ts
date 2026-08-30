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
