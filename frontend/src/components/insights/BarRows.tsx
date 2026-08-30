import { useEffect, useRef, useState } from 'react'
import { barWidthPct, segmentsFor } from '@/lib/insights'
import { fmtPaise } from '@/lib/format'
import type { CauseBucket, ExceptionRecord } from '@/lib/types'

/* Horizontal bars built in plain HTML rather than laid-out SVG: labels wrap
   and reflow for free, which is what actually prevents collisions at narrow
   widths. Order lives on the axis (rows are sorted), so colour is free to
   encode the one thing that matters -- whether a bucket is past the router's
   abandonment bound.

   D3 drew these in the version this replaces, but only for its keyed join and
   width tween. React's reconciliation is the join, and the width transition
   lives in the stylesheet, so the dependency buys nothing here. */

export interface BarRow {
  key: string
  label: string
  amountPaise: number
  value: string
  sub?: string
  title?: string
  alarm?: boolean
}

function Fill({ segments, width }: { segments: ExceptionRecord[] | null; width: string }) {
  if (!segments) return <div className="bar-fill" style={{ width }} />
  return (
    <div className="bar-fill segmented" style={{ width }}>
      {segments.map((r) => (
        <div
          key={r.id}
          className="seg"
          // Normalised to sum to 100 rather than passed raw paise: keeps the
          // serialised value readable instead of 1.75e+06, and stays clear of
          // the flex rule that a total grow below 1 claims only that fraction
          // of the free space.
          style={{
            flexGrow:
              (r.amount_paise /
                segments.reduce((t, x) => t + x.amount_paise, 0)) *
              100,
          }}
          title={`${r.ledger_ref || r.exception_key} — ${fmtPaise(r.amount_paise)}`}
        />
      ))}
    </div>
  )
}

export function BarRows({
  rows,
  empty,
  /** Backlog-by-cause only: the records behind each bar, sliced into the fill. */
  recordsByCause,
  buckets,
}: {
  rows: BarRow[]
  empty: string
  recordsByCause?: Map<string, ExceptionRecord[]>
  buckets?: CauseBucket[]
}) {
  // One measurement for the whole chart -- every track is the same width, so
  // this costs a single reflow rather than one per row. Segmenting needs a
  // real pixel width, which only exists after layout.
  const trackRef = useRef<HTMLDivElement>(null)
  const [trackPx, setTrackPx] = useState(0)

  useEffect(() => {
    const node = trackRef.current
    if (!node) return
    const measure = () => setTrackPx(node.clientWidth)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(node)
    return () => ro.disconnect()
  }, [rows.length])

  if (!rows.length) return <div className="chart-empty">{empty}</div>

  const max = Math.max(...rows.map((r) => r.amountPaise), 1)

  return (
    <>
      {rows.map((row, i) => {
        const width = barWidthPct(row.amountPaise, max)
        const bucket = buckets?.find((b) => b.cause === row.key)
        const segments =
          bucket && recordsByCause
            ? segmentsFor(bucket, recordsByCause.get(row.key), trackPx * (row.amountPaise / max))
            : null

        return (
          <div
            key={row.key}
            className={`bar-row${row.alarm ? ' past-bound' : ''}`}
            title={row.title}
          >
            <div className="bar-label">{row.label}</div>
            <div className="bar-track" ref={i === 0 ? trackRef : undefined}>
              <Fill segments={segments} width={width} />
            </div>
            <div className="bar-value">
              <span>{row.value}</span>
              {row.sub && <span className="sub">{row.sub}</span>}
            </div>
          </div>
        )
      })}
    </>
  )
}
