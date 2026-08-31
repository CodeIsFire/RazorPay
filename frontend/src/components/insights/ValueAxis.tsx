import { useChartStable } from '@/components/charts/chart-context'
import { fmtCompact } from '@/lib/insights'

/* Bklit draws the gridlines but labels no values against them: its BarYAxis is
   the category axis, which for a vertical chart is the day, not the money. An
   unlabelled grid turns the columns into a shape with no scale, so the ticks
   are drawn here from the chart's own y-scale -- same five stops as the
   gridlines, same compact rupee formatting the chart had before. */
const TICK_COUNT = 5

/* These ticks are positioned in the chart's INNER coordinate space, not the
   outer SVG's. BarChart renders every child inside
   <g transform="translate(margin.left, margin.top)">, so the plot's own origin
   is already (0,0) here. Adding the margin again -- which this component used
   to do, via x={margin.left - 8} and y={margin.top + ...} -- offset the labels
   by the margin twice and drew them a full margin-width INSIDE the plot, where
   "₹0" landed on top of the first column. x is negative on purpose: it reaches
   back out into the left gutter, which is what the margin is reserved for. */
export function ValueAxis() {
  const { yScale, innerHeight } = useChartStable()
  if (!yScale) return null

  const [, max] = yScale.domain()

  return (
    <g aria-hidden="true">
      {Array.from({ length: TICK_COUNT }, (_, i) => {
        const value = (max * (TICK_COUNT - 1 - i)) / (TICK_COUNT - 1)
        const y = (innerHeight * i) / (TICK_COUNT - 1)
        return (
          <text
            key={i}
            className="tick"
            x={-8}
            // +3.5 puts the cap-height of a 10px face on the line rather than
            // hanging its baseline off it.
            y={y + 3.5}
            textAnchor="end"
          >
            {fmtCompact(value)}
          </text>
        )
      })}
    </g>
  )
}
