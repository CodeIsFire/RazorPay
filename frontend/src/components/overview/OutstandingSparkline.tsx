import { LinearGradient } from '@visx/gradient'
import { ParentSize } from '@visx/responsive'
import { scaleLinear } from '@visx/scale'
import { AreaClosed, LinePath } from '@visx/shape'
import { useId, useMemo, useState } from 'react'
import type { DailyRow } from '@/lib/types'

/* How the unreconciled total has moved, day by day.

   Adapted from Bklit's stat-card-area block rather than installed from it. The
   registry entry pulls @bklit/area-chart, which writes loading-sweep.tsx and
   chart-child-passthrough.ts -- files this project already vendors and has
   since fixed bugs in -- plus shadcn's card/badge, whose tokens (--card,
   --border, --ring) do not exist here. The idea was worth taking; the install
   was not. This uses the visx primitives already in package.json.

   Deliberately linear between points, not a cardinal spline. A smoothed curve
   overshoots between samples, and on a ledger balance that would draw amounts
   that were never outstanding on any day. Straight segments between real
   observations is the honest reading. */

export interface SparkPoint {
  day: string
  paise: number
}

export function OutstandingSparkline({
  days,
  onHover,
  height = 56,
}: {
  days: DailyRow[]
  /** Fires with the hovered point, or null on leave, so the headline figure
      above can retarget to the day under the cursor. */
  onHover?: (point: SparkPoint | null) => void
  height?: number
}) {
  const gradientId = useId()
  const [activeIndex, setActiveIndex] = useState<number | null>(null)

  const points = useMemo(
    () => days.map((d) => ({ day: d.day, paise: d.outstanding_paise })),
    [days],
  )
  // Outside the ParentSize callback below: it depends only on the data, and
  // that callback re-runs on every debounced resize tick.
  const max = useMemo(() => Math.max(...points.map((p) => p.paise), 1), [points])

  // Two points is the minimum that describes a direction; one is a dot, and a
  // dot pretending to be a trend line is worse than no chart.
  if (points.length < 2) return null

  return (
    <div
      className="spark"
      style={{ height }}
      /* The whole hero card is a <button> that opens Needs attention, and this
         chart sits inside it. Without this, scrubbing a day navigates away --
         and on touch it is worse: onPointerMove only fires during a drag, so a
         tap to read a value ONLY navigates. Scrubbing and opening are different
         intentions and must not share a click. */
      onClick={(event) => event.stopPropagation()}
    >
      <ParentSize debounceTime={10}>
        {({ width }) => {
          if (width < 40) return null

          const x = scaleLinear({ domain: [0, points.length - 1], range: [0, width] })
          // Floor at 2px so a zero day still reads as a baseline rather than
          // vanishing into the axis.
          const y = scaleLinear({ domain: [0, max], range: [height - 2, 2] })

          const move = (event: React.PointerEvent<SVGSVGElement>) => {
            const rect = event.currentTarget.getBoundingClientRect()
            const ratio = (event.clientX - rect.left) / rect.width
            const index = Math.max(
              0,
              Math.min(points.length - 1, Math.round(ratio * (points.length - 1))),
            )
            setActiveIndex(index)
            onHover?.(points[index])
          }

          const leave = () => {
            setActiveIndex(null)
            onHover?.(null)
          }

          const active = activeIndex === null ? null : points[activeIndex]

          return (
            <svg
              width={width}
              height={height}
              // Pointer events rather than mouse events so this scrubs on
              // touch and pen without a second code path.
              onPointerMove={move}
              onPointerLeave={leave}
              role="img"
              aria-label={`Unreconciled balance across the last ${points.length} business days`}
            >
              <LinearGradient
                id={gradientId}
                from="var(--orange)"
                fromOpacity={0.28}
                to="var(--orange)"
                toOpacity={0}
              />
              <AreaClosed
                data={points}
                x={(_, i) => x(i)}
                y={(p) => y(p.paise)}
                yScale={y}
                fill={`url(#${gradientId})`}
                stroke="none"
              />
              <LinePath
                data={points}
                x={(_, i) => x(i)}
                y={(p) => y(p.paise)}
                stroke="var(--orange)"
                strokeWidth={1.75}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {active && activeIndex !== null && (
                <g>
                  <line
                    x1={x(activeIndex)}
                    x2={x(activeIndex)}
                    y1={0}
                    y2={height}
                    stroke="var(--border-strong)"
                    strokeWidth={1}
                  />
                  <circle
                    cx={x(activeIndex)}
                    cy={y(active.paise)}
                    r={3.5}
                    fill="var(--orange)"
                    stroke="var(--surface-1)"
                    strokeWidth={2}
                  />
                </g>
              )}
            </svg>
          )
        }}
      </ParentSize>
    </div>
  )
}
