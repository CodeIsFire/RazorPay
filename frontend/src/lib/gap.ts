import type { DailyRow } from './types'

/** Totals are summed from /analytics/daily so all three figures are
    ledger-side and consistent. This is deliberately a NARROWER number than the
    Insights "Value at risk" tile, which also counts gateway-side orphans --
    two questions, two totals, both correct. */
export function gapTotals(days: DailyRow[]) {
  const sum = (k: keyof DailyRow) => days.reduce((a, d) => a + ((d[k] as number) || 0), 0)
  return {
    expected: sum('total_paise'),
    settled: sum('reconciled_paise'),
    gap: sum('outstanding_paise'),
  }
}

/** The two segments share one track, so the gap is clamped to what the settled
    fill leaves behind rather than to 100 -- otherwise a rounding disagreement
    between the two could sum past the track and push the gap off the end. */
export function gapBarPercents(expected: number, settled: number, gap: number) {
  if (expected <= 0) return { settledPct: 0, gapPct: 0 }
  const settledPct = Math.max(0, Math.min(100, (settled / expected) * 100))
  const gapPct = Math.max(0, Math.min(100 - settledPct, (gap / expected) * 100))
  return { settledPct, gapPct }
}

/** The identity of a rendered bar. Two polls that produce the same key must
    not restart the reveal animation -- rounded to 2dp because that is the
    precision the widths are actually written at, so a difference finer than
    the rendered value is not a change. */
export function gapBarKey(settledPct: number, gapPct: number) {
  return `${settledPct.toFixed(2)}/${gapPct.toFixed(2)}`
}
