import type { Funnel } from './types'

/* The reconciliation flow: what happened to every ledger row that came in.

   `/funnel` guarantees matched + exceptions + recovered == ingested, and
   app/funnel.py asserts it. That identity is the whole point of this
   visualisation -- the segment widths ARE the invariant, so a reader can see
   that the parts account for the whole rather than being told they do.

   Two things this deliberately does NOT do:

   - It never mixes in money. The hero above it is denominated in paise and
     sums /analytics/daily; this is a count of ledger rows from /funnel. They
     answer different questions and their totals legitimately differ, which is
     the same discipline lib/gap.ts documents. Two units, two visuals.
   - It never folds `gateway_side_anomalies` into the bar. Those rows have no
     ledger row at all, so they are outside the identity above -- adding them
     as a fourth segment would make the widths stop summing to the whole and
     quietly turn a true statement into a false one. They are reported beside
     the bar instead. */

export type FlowKey = 'matched' | 'recovered' | 'exceptions'

export interface FlowSegment {
  key: FlowKey
  label: string
  count: number
  /** Width as a percentage of `ingested`. The three always sum to 100. */
  pct: number
}

const LABELS: Record<FlowKey, string> = {
  matched: 'Auto-matched',
  recovered: 'Recovered',
  exceptions: 'Need attention',
}

/** Ordered as the work flows: settled without help, settled by the agent, still
    open. That puts the only actionable segment at the end of the bar, where it
    reads as the remainder rather than as one category among three. */
const ORDER: FlowKey[] = ['matched', 'recovered', 'exceptions']

export function flowSegments(funnel: Funnel | undefined): FlowSegment[] {
  if (!funnel || funnel.ingested <= 0) return []

  const counts: Record<FlowKey, number> = {
    matched: funnel.matched,
    recovered: funnel.recovered,
    exceptions: funnel.exceptions,
  }

  // Whole percentages, apportioned by largest remainder so the three total
  // exactly 100 and the bar always fills its track. Integers because these are
  // read as much as they are drawn, and a legend that mixes "41%" with "30.3%"
  // looks like two different measurements rather than one breakdown; the
  // largest-remainder split keeps every share within a point of its true value.
  const exact = ORDER.map((key) => ({ key, value: (counts[key] / funnel.ingested) * 100 }))
  const rows = exact.map((r) => ({ ...r, pct: Math.floor(r.value), rem: r.value - Math.floor(r.value) }))

  let shortfall = 100 - rows.reduce((sum, r) => sum + r.pct, 0)
  // Award the remaining points to the largest fractional parts. A zero-count
  // segment has no remainder, so it can never be handed a sliver of width for
  // records that do not exist.
  const byRemainder = [...rows].sort((a, b) => b.rem - a.rem)
  for (const row of byRemainder) {
    if (shortfall <= 0) break
    if (counts[row.key] === 0) continue
    row.pct += 1
    shortfall -= 1
  }

  return ORDER.map((key) => {
    const row = rows.find((r) => r.key === key)!
    return { key, label: LABELS[key], count: counts[key], pct: row.pct }
  })
}

/** Identity of a rendered bar, so a poll returning the same shape does not
    restart the reveal. Rounded to the precision the widths are written at. */
export function flowSignature(segments: FlowSegment[]): string {
  return segments.map((s) => `${s.key}:${s.pct}`).join('|')
}

/** True when the backend's own invariant holds for this payload. The bar is
    only honest if it does, so the component falls back to a plain reading of
    the counts when it does not rather than drawing a bar that lies. */
export function flowAccountsForAll(funnel: Funnel | undefined): boolean {
  if (!funnel) return false
  return funnel.matched + funnel.recovered + funnel.exceptions === funnel.ingested
}
