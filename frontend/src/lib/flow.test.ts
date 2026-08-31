import { describe, expect, it } from 'vitest'
import { flowAccountsForAll, flowSegments, flowSignature } from './flow'
import type { Funnel } from './types'

const funnel = (over: Partial<Funnel> = {}): Funnel => ({
  ingested: 66,
  matched: 27,
  exceptions: 19,
  recovered: 20,
  match_rate: 0.4091,
  amount_recovered_paise: 62377100,
  gateway_side_anomalies: 16,
  ...over,
})

describe('flowSegments', () => {
  it('orders the segments as the work flows', () => {
    expect(flowSegments(funnel()).map((s) => s.key)).toEqual([
      'matched',
      'recovered',
      'exceptions',
    ])
  })

  it('always fills the track exactly', () => {
    // The bar's claim is that the parts account for the whole. If the widths
    // summed to 99.9 the track would show a sliver of unexplained gap, which
    // is precisely the thing this visual exists to rule out.
    const total = flowSegments(funnel()).reduce((sum, s) => sum + s.pct, 0)
    expect(total).toBeCloseTo(100, 5)
  })

  it('still fills exactly for counts that do not divide cleanly', () => {
    for (const [ingested, matched, recovered, exceptions] of [
      [3, 1, 1, 1],
      [7, 3, 2, 2],
      [1000, 333, 333, 334],
      [11, 10, 1, 0],
    ]) {
      const segments = flowSegments(funnel({ ingested, matched, recovered, exceptions }))
      const total = segments.reduce((sum, s) => sum + s.pct, 0)
      expect(total).toBeCloseTo(100, 5)
    }
  })

  it('never gives width to a segment with no records', () => {
    // Rounding remainder goes to the largest share, so an empty category must
    // not pick up a stray sliver and imply records that do not exist.
    const segments = flowSegments(funnel({ ingested: 11, matched: 10, recovered: 1, exceptions: 0 }))
    expect(segments.find((s) => s.key === 'exceptions')).toMatchObject({ count: 0, pct: 0 })
  })

  it('reports nothing rather than an empty bar when no ledger has been ingested', () => {
    expect(flowSegments(funnel({ ingested: 0, matched: 0, recovered: 0, exceptions: 0 }))).toEqual([])
    expect(flowSegments(undefined)).toEqual([])
  })

  it("carries each segment's real count, not its percentage", () => {
    const segments = flowSegments(funnel())
    expect(segments.map((s) => s.count)).toEqual([27, 20, 19])
  })
})

describe('flowAccountsForAll', () => {
  it('holds for a well-formed funnel', () => {
    expect(flowAccountsForAll(funnel())).toBe(true)
  })

  it('fails when the parts do not sum to the whole', () => {
    // gateway_side_anomalies sits OUTSIDE the identity on purpose; folding it
    // in has to be detectable rather than silently drawn.
    expect(flowAccountsForAll(funnel({ ingested: 82 }))).toBe(false)
  })

  it('is false with no data at all', () => {
    expect(flowAccountsForAll(undefined)).toBe(false)
  })
})

describe('flowSignature', () => {
  it('is stable across polls that render identically', () => {
    expect(flowSignature(flowSegments(funnel()))).toBe(flowSignature(flowSegments(funnel())))
  })

  it('changes when a segment actually moves', () => {
    const before = flowSignature(flowSegments(funnel()))
    const after = flowSignature(flowSegments(funnel({ matched: 28, exceptions: 18 })))
    expect(after).not.toBe(before)
  })
})
