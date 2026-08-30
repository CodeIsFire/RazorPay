import { useMemo, useState } from 'react'
import { Bar } from '@/components/charts/bar'
import { BarChart } from '@/components/charts/bar-chart'
import { BarXAxis } from '@/components/charts/bar-x-axis'
import { ChartLegendHoverProvider } from '@/components/charts/chart-legend-hover'
import { Grid } from '@/components/charts/grid'
import { SeriesLegend, type SeriesLegendItem } from '@/components/insights/SeriesLegend'
import { ValueAxis } from '@/components/insights/ValueAxis'
import { fmtPaise } from '@/lib/format'
import { fmtCompact } from '@/lib/insights'
import type { DailyRow } from '@/lib/types'

/* Series order below is load-bearing: Bar compares the hovered index against
   its own position among the chart's children, so these must match the order
   the <Bar> elements are written in, not the order the legend reads in. */
const RECONCILED_SERIES = 0
const OUTSTANDING_SERIES = 1

const LEGEND: SeriesLegendItem[] = [
  { label: 'Still outstanding', color: 'var(--chart-bar)', seriesIndex: OUTSTANDING_SERIES },
  { label: 'Reconciled', color: 'var(--chart-context)', seriesIndex: RECONCILED_SERIES },
]

/* Ledger value per business day, split into what reconciled and what is still
   outstanding. The reconciled share wears the de-emphasis grey and the
   outstanding share -- the only part anybody can act on -- keeps the accent.
   One hue plus grey, so there is no categorical palette here to validate.

   The 2px stackGap is a surface gap between the two fills, never a border:
   a border would add to the column's height and misstate the value. */
const SEG_GAP_PX = 2

export function DailyTimeline({ days }: { days: DailyRow[] }) {
  const data = useMemo(
    () =>
      days.map((d) => ({
        ...d,
        label: new Date(d.day + 'T00:00:00Z').toLocaleDateString(undefined, {
          day: 'numeric',
          month: 'short',
          timeZone: 'UTC',
        }),
      })),
    [days],
  )

  // Hover (or focus) isolates a series transiently; a click sticks it, which
  // is the only way this works on touch, where hover never fires.
  const [hovered, setHovered] = useState<number | null>(null)
  const [sticky, setSticky] = useState<number | null>(null)
  const active = hovered ?? sticky

  if (!days.length) {
    return <div className="chart-empty">No ledger activity yet.</div>
  }

  const maxTotal = Math.max(...days.map((d) => d.total_paise), 1)

  return (
    <>
      <ChartLegendHoverProvider hoveredIndex={active} onHoverChange={setHovered}>
        <BarChart
          data={data}
          xDataKey="label"
          stacked
          stackGap={SEG_GAP_PX}
          aspectRatio="16 / 7"
          barWidth={46}
          margin={{ left: 52, right: 6, top: 10, bottom: 4 }}
        >
          {/* Solid hairlines -- dashes read as "threshold" when this is just a scale. */}
          <Grid horizontal numTicksRows={5} strokeDasharray="" />
          <ValueAxis />
          {/* Reconciled sits underneath as context; outstanding stacks on top so
              the actionable share is the part that reads against the gridlines. */}
          <Bar dataKey="reconciled_paise" fill="var(--chart-context)" lineCap={0} />
          <Bar dataKey="outstanding_paise" fill="var(--chart-bar)" lineCap="round" />
          {/* Thinned rather than allowed to collide -- every day is still named
              in the accessible table below. */}
          <BarXAxis maxLabels={10} />
        </BarChart>
      </ChartLegendHoverProvider>

      <SeriesLegend
        items={LEGEND}
        active={active}
        sticky={sticky}
        onActivate={setHovered}
        onToggleSticky={(i) => setSticky((cur) => (cur === i ? null : i))}
      />

      {/* Reachable by assistive tech, absent for everyone else. Without this
          the figures live only in hover tooltips, which a screen reader and a
          keyboard user never reach. It does not come free with the chart
          library, so it is built by hand exactly as it was before. */}
      <table className="sr-only">
        <caption>
          Ledger value per business day, split into reconciled and still outstanding
        </caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Total</th>
            <th scope="col">Reconciled</th>
            <th scope="col">Still outstanding</th>
            <th scope="col">Payouts</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.day}>
              <th scope="row">{d.label}</th>
              <td>{fmtPaise(d.total_paise)}</td>
              <td>{fmtPaise(d.reconciled_paise)}</td>
              <td>{fmtPaise(d.outstanding_paise)}</td>
              <td>{d.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <span className="sr-only">Largest day: {fmtCompact(maxTotal)}</span>
    </>
  )
}
