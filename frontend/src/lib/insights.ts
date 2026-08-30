import type { CauseBucket, ExceptionRecord } from './types'

/** Mirrors NON_TERMINAL_STATUSES in app/analytics.py. This is the one business
    rule deliberately duplicated client-side, so that the per-record slices and
    the server's own by_cause totals are counting the same population -- which
    is exactly what segmentsFor() checks below before it draws a composition. */
export const BACKLOG_STATUSES = new Set(['open', 'pending'])

/** One slice per underlying record, largest first. */
export function groupBacklogByCause(
  entries: ExceptionRecord[],
): Map<string, ExceptionRecord[]> {
  const byCause = new Map<string, ExceptionRecord[]>()
  for (const e of entries) {
    if (!BACKLOG_STATUSES.has(e.status)) continue
    const list = byCause.get(e.cause)
    if (list) list.push(e)
    else byCause.set(e.cause, [e])
  }
  return byCause
}

/** Minimum width a slice may render at before the composition stops being
    readable and a solid bar says more. */
export const SEG_MIN_PX = 3

/* The signature of the Backlog-by-cause chart: one slice per underlying
   record, so the bar shows how the money is composed and not just how much.
   Fourteen thin slivers and four fat blocks can carry the same total -- that
   difference is the whole point, and it is invisible in a solid bar.

   Two conditions have to hold before slicing, and both fall back to a solid
   fill rather than drawing something misleading:

   - The client-side grouping must AGREE with the server's own aggregate. The
     bar length and the printed total come from by_cause; if the records we
     hold don't reproduce it (a poll landing between the two queries, a status
     changing underneath), the composition would be a lie told at pixel
     precision.
   - The slices must be wide enough to read, allowing for the 1px gaps. */
export function segmentsFor(
  bucket: CauseBucket,
  records: ExceptionRecord[] | undefined,
  barPx: number,
): ExceptionRecord[] | null {
  const recs = (records ?? []).slice().sort((a, b) => b.amount_paise - a.amount_paise)
  if (!recs.length) return null

  const sum = recs.reduce((t, x) => t + x.amount_paise, 0)
  const agrees = recs.length === bucket.count && sum === bucket.amount_paise
  if (!agrees) return null

  const roomy = (barPx - (recs.length - 1)) / recs.length >= SEG_MIN_PX
  if (!roomy) return null

  return recs
}

/** Bars are scaled against the largest value in their own chart, not against
    a shared axis: each chart answers its own question. */
export function barWidthPct(value: number, max: number): string {
  return `${((value / (max || 1)) * 100).toFixed(1)}%`
}

export function fmtCompact(paise: number): string {
  const rupees = paise / 100
  if (rupees >= 100000) return '₹' + (rupees / 100000).toFixed(1) + 'L'
  if (rupees >= 1000) return '₹' + Math.round(rupees / 1000) + 'k'
  return '₹' + Math.round(rupees)
}
