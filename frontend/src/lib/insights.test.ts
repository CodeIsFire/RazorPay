import { describe, expect, it } from 'vitest'
import {
  barWidthPct,
  fmtCompact,
  groupBacklogByCause,
  SEG_MIN_PX,
  segmentsFor,
} from './insights'
import type { CauseBucket, ExceptionRecord } from './types'

let seq = 0
const rec = (over: Partial<ExceptionRecord> = {}): ExceptionRecord => ({
  id: ++seq,
  exception_key: `k${seq}`,
  cause: 'failed_payment',
  ledger_ref: `LED-${seq}`,
  gateway_ref: null,
  matched_source: 'gateway',
  amount_paise: 1000,
  detail: null,
  status: 'open',
  retry_count: 0,
  created_at: '2026-08-20 10:00:00',
  updated_at: '2026-08-20 10:00:00',
  ...over,
})

const bucket = (over: Partial<CauseBucket> = {}): CauseBucket => ({
  cause: 'failed_payment',
  count: 2,
  amount_paise: 2000,
  share: 0.5,
  ...over,
})

describe('groupBacklogByCause', () => {
  it('keeps only what is still backlog', () => {
    // Mirrors NON_TERMINAL_STATUSES in app/analytics.py: resolved and
    // abandoned are history, not backlog.
    const rows = [
      rec({ status: 'open' }),
      rec({ status: 'pending' }),
      rec({ status: 'resolved' }),
      rec({ status: 'abandoned' }),
    ]
    expect(groupBacklogByCause(rows).get('failed_payment')).toHaveLength(2)
  })

  it('separates the causes', () => {
    const out = groupBacklogByCause([
      rec({ cause: 'failed_payment' }),
      rec({ cause: 'chargeback' }),
    ])
    expect([...out.keys()].sort()).toEqual(['chargeback', 'failed_payment'])
  })
})

describe('segmentsFor', () => {
  const wide = 400

  it('slices a bar into one piece per record, largest first', () => {
    const records = [rec({ amount_paise: 500 }), rec({ amount_paise: 1500 })]
    const out = segmentsFor(bucket({ count: 2, amount_paise: 2000 }), records, wide)
    expect(out?.map((r) => r.amount_paise)).toEqual([1500, 500])
  })

  it('falls back to a solid bar when the records disagree with the server total', () => {
    // The bar length and printed total come from by_cause. If the records we
    // hold don't reproduce it -- a poll landing between the two queries -- a
    // composition would be a lie told at pixel precision.
    const records = [rec({ amount_paise: 500 })]
    expect(segmentsFor(bucket({ count: 2, amount_paise: 2000 }), records, wide)).toBeNull()
  })

  it('falls back when the count agrees but the sum does not', () => {
    const records = [rec({ amount_paise: 500 }), rec({ amount_paise: 400 })]
    expect(segmentsFor(bucket({ count: 2, amount_paise: 2000 }), records, wide)).toBeNull()
  })

  it('falls back rather than drawing slivers too thin to read', () => {
    const many = Array.from({ length: 50 }, () => rec({ amount_paise: 100 }))
    const b = bucket({ count: 50, amount_paise: 5000 })
    expect(segmentsFor(b, many, 60)).toBeNull()
    // Given room, the same data does slice.
    expect(segmentsFor(b, many, 50 * (SEG_MIN_PX + 1) + 50)).toHaveLength(50)
  })

  it('falls back when there is nothing behind the bar', () => {
    expect(segmentsFor(bucket(), [], wide)).toBeNull()
    expect(segmentsFor(bucket(), undefined, wide)).toBeNull()
  })

  it('does not reorder the caller’s array', () => {
    const a = rec({ amount_paise: 500 })
    const b = rec({ amount_paise: 1500 })
    const input = [a, b]
    segmentsFor(bucket({ count: 2, amount_paise: 2000 }), input, wide)
    expect(input[0]).toBe(a)
  })
})

describe('barWidthPct', () => {
  it('scales against the largest value in its own chart', () => {
    expect(barWidthPct(50, 200)).toBe('25.0%')
    expect(barWidthPct(200, 200)).toBe('100.0%')
  })

  it('survives an all-zero chart without dividing by zero', () => {
    expect(barWidthPct(0, 0)).toBe('0.0%')
  })
})

describe('fmtCompact', () => {
  it('uses lakhs past a hundred thousand rupees', () => {
    expect(fmtCompact(20_000_000)).toBe('₹2.0L')
  })

  it('uses thousands in between', () => {
    expect(fmtCompact(5_000_000)).toBe('₹50k')
  })

  it('prints small amounts whole', () => {
    expect(fmtCompact(45_600)).toBe('₹456')
  })
})
