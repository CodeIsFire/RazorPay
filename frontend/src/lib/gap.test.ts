import { describe, expect, it } from 'vitest'
import { gapBarKey, gapBarPercents, gapTotals } from './gap'
import type { DailyRow } from './types'

const day = (over: Partial<DailyRow>): DailyRow => ({
  day: '2026-08-20',
  total_paise: 0,
  outstanding_paise: 0,
  reconciled_paise: 0,
  count: 0,
  ...over,
})

describe('gapTotals', () => {
  it('sums each column across every day', () => {
    const days = [
      day({ total_paise: 1000, reconciled_paise: 600, outstanding_paise: 400 }),
      day({ total_paise: 500, reconciled_paise: 500, outstanding_paise: 0 }),
    ]
    expect(gapTotals(days)).toEqual({ expected: 1500, settled: 1100, gap: 400 })
  })

  it('reports zeroes for an empty ledger rather than NaN', () => {
    expect(gapTotals([])).toEqual({ expected: 0, settled: 0, gap: 0 })
  })
})

describe('gapBarPercents', () => {
  it('splits the track between settled and outstanding', () => {
    expect(gapBarPercents(1000, 600, 400)).toEqual({ settledPct: 60, gapPct: 40 })
  })

  it('renders nothing when there is no ledger activity', () => {
    // Guards against dividing by zero and painting a NaN% width.
    expect(gapBarPercents(0, 0, 0)).toEqual({ settledPct: 0, gapPct: 0 })
  })

  it('keeps the gap inside whatever the settled fill leaves behind', () => {
    // Both segments share one track. If the two figures disagree -- which they
    // can, since they are summed independently -- the gap must not push past
    // the end of the track.
    const { settledPct, gapPct } = gapBarPercents(1000, 900, 400)
    expect(settledPct).toBe(90)
    expect(gapPct).toBe(10)
    expect(settledPct + gapPct).toBeLessThanOrEqual(100)
  })

  it('clamps a settled figure that exceeds what was expected', () => {
    expect(gapBarPercents(1000, 1200, 0).settledPct).toBe(100)
  })
})

describe('gapBarKey', () => {
  it('is stable for the same rendered widths, so a poll does not replay the reveal', () => {
    expect(gapBarKey(67.654321, 32.345678)).toBe(gapBarKey(67.654999, 32.345001))
  })

  it('changes once the widths differ at the precision they are rendered at', () => {
    expect(gapBarKey(67.65, 32.35)).not.toBe(gapBarKey(67.66, 32.34))
  })
})
