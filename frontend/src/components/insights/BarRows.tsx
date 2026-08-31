import { useEffect, useRef, useState } from 'react'
import { useFirstPaintReveal } from '@/hooks/useFirstPaintReveal'
import { barWidthPct, segmentsFor } from '@/lib/insights'
import { fmtPaise } from '@/lib/format'
import type { CauseBucket, ExceptionRecord } from '@/lib/types'


/* Horizontal bars built in plain HTML rather than laid-out SVG: labels wrap
   and reflow for free, which is what actually prevents collisions at narrow
   widths. Order lives on the axis (rows are sorted), so colour is free to
   encode the one thing that matters -- whether a bucket is past the router's
   abandonment bound.

   D3 drew these in the version this replaces, but only for its keyed join and
   width tween. React's reconciliation is the join, so the dependency buys
   nothing here.

   The tween did not survive, and is deliberately not being restored: these
   bars size by width, and animating width is a layout animation on every
   frame for every row. The bars snap when the 15s poll changes them. If that
   ever needs softening, it wants a transform on a full-width fill, not a
   width transition. */

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
  /** Makes each row a control that drills into the records behind it. Only
      supplied where a row maps cleanly onto a filter the exceptions table can
      actually apply -- see the note on the call site in InsightsTab. */
  onSelect,
  selectHint,
}: {
  rows: BarRow[]
  empty: string
  recordsByCause?: Map<string, ExceptionRecord[]>
  buckets?: CauseBucket[]
  onSelect?: (key: string) => void
  selectHint?: (row: BarRow) => string
}) {
  // One measurement for the whole chart -- every track is the same width, so
  // this costs a single reflow rather than one per row. Segmenting needs a
  // real pixel width, which only exists after layout.
  const trackRef = useRef<HTMLDivElement>(null)
  const [trackPx, setTrackPx] = useState(0)

  // One pass, reused by both the reveal signature and the render below. The
  // signature previously called a helper that recomputed this max per row --
  // O(n^2) on every render, and the same value was then derived a second time
  // for rendering. Rows are few, but this runs on every parent re-render
  // (legend hover, 15s poll), not only when the data changes.
  const maxPaise = Math.max(...rows.map((r) => r.amountPaise), 1)

  /* The bars wipe in once, on first paint, and never again. The comment above
     rules out animating width -- that is a layout pass per frame per row -- and
     scaleX on the fill is ruled out too here: the segmented variant lays its
     slices out with 1px flex gaps, and scaling the parent would squash those
     seams to fractional pixels. A clip-path wipe on the track leaves every
     fill at its true geometry and runs on the compositor.

     The signature is the rendered widths, so a 15s poll that changes nothing
     visible does not restart the sweep -- the same guarantee the gap hero
     makes, and the reason this file previously chose to let bars snap. */
  const revealed = useFirstPaintReveal(
    rows.map((r) => `${r.key}:${Math.round((r.amountPaise / maxPaise) * 100)}`).join('|'),
  )

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


  return (
    <div className={`bar-rows${revealed ? ' shown' : ''}`}>
      {rows.map((row, i) => {
        const width = barWidthPct(row.amountPaise, maxPaise)
        const bucket = buckets?.find((b) => b.cause === row.key)
        const segments =
          bucket && recordsByCause
            ? segmentsFor(bucket, recordsByCause.get(row.key), trackPx * (row.amountPaise / maxPaise))
            : null

        const body = (
          <>
            <div className="bar-label">{row.label}</div>
            <div className="bar-track" ref={i === 0 ? trackRef : undefined}>
              <Fill segments={segments} width={width} />
            </div>
            <div className="bar-value">
              <span>{row.value}</span>
              {row.sub && <span className="sub">{row.sub}</span>}
            </div>
          </>
        )

        if (!onSelect) {
          return (
            <div
              key={row.key}
              className={`bar-row${row.alarm ? ' past-bound' : ''}`}
              title={row.title}
            >
              {body}
            </div>
          )
        }

        return (
          <button
            key={row.key}
            type="button"
            className={`bar-row clickable${row.alarm ? ' past-bound' : ''}`}
            title={selectHint?.(row) ?? row.title}
            onClick={() => onSelect(row.key)}
          >
            {body}
          </button>
        )
      })}
    </div>
  )
}
