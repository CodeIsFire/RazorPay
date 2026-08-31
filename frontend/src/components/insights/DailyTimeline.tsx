import { useMemo, useState } from 'react'
import { Bar } from '@/components/charts/bar'
import { BarChart } from '@/components/charts/bar-chart'
import { BarXAxis } from '@/components/charts/bar-x-axis'
import { ChartLegendHoverProvider } from '@/components/charts/chart-legend-hover'
import { Grid } from '@/components/charts/grid'
import { ChartTooltip } from '@/components/charts/tooltip'
import { SeriesLegend, type SeriesLegendItem } from '@/components/insights/SeriesLegend'
import { ValueAxis } from '@/components/insights/ValueAxis'
import { LoadingAnnounce } from '@/components/ui/Skeleton'
import { fmtDay, fmtPaise } from '@/lib/format'
import { fmtCompact } from '@/lib/insights'
import type { DailyRow } from '@/lib/types'

/* Series order below is load-bearing: Bar compares the hovered index against
   its own position among the chart's children, so these must match the order
   the <Bar> elements are written in, not the order the legend reads in. */
const RECONCILED_SERIES = 0
const OUTSTANDING_SERIES = 1

/* The legend carries each series' total for the window on screen, not just its
   colour. A key that only says which colour is which makes the reader estimate
   the split off the column heights; stating it turns the legend into the
   chart's summary line. Adapted from Bklit's legend block, which pairs a marker
   with a value and a share -- built on the existing SeriesLegend rather than
   installed, since that component's isolate-on-click behaviour is test-locked
   and the registry entry would have overwritten src/lib/utils.ts and three
   chart files this project already has. */
function legendFor(days: DailyRow[]): SeriesLegendItem[] {
  const outstanding = days.reduce((sum, d) => sum + d.outstanding_paise, 0)
  const reconciled = days.reduce((sum, d) => sum + d.reconciled_paise, 0)
  const total = outstanding + reconciled

  // Shares are of the two series together, which is the whole ledger value on
  // screen -- so the two percentages sum to 100 and describe this chart rather
  // than some other total elsewhere on the page.
  const share = (part: number) => (total > 0 ? part / total : undefined)

  return [
    {
      label: 'Still outstanding',
      color: 'var(--chart-bar)',
      seriesIndex: OUTSTANDING_SERIES,
      value: fmtPaise(outstanding),
      share: share(outstanding),
    },
    {
      label: 'Reconciled',
      color: 'var(--chart-context)',
      seriesIndex: RECONCILED_SERIES,
      value: fmtPaise(reconciled),
      share: share(reconciled),
    },
  ]
}

/* Ledger value per business day, split into what reconciled and what is still
   outstanding. The reconciled share wears the de-emphasis grey and the
   outstanding share -- the only part anybody can act on -- keeps the accent.
   One hue plus grey, so there is no categorical palette here to validate.

   The 2px stackGap is a surface gap between the two fills, never a border:
   a border would add to the column's height and misstate the value. */
const SEG_GAP_PX = 2

export function DailyTimeline({ days, loading }: { days: DailyRow[]; loading?: boolean }) {
  const data = useMemo(
    () =>
      days.map((d) => ({
        ...d,
        label: fmtDay(d.day),
      })),
    [days],
  )

  // Hover (or focus) isolates a series transiently; a click sticks it, which
  // is the only way this works on touch, where hover never fires.
  const [hovered, setHovered] = useState<number | null>(null)
  const [sticky, setSticky] = useState<number | null>(null)
  const active = hovered ?? sticky

  /* Loading is checked before empty, and the distinction matters: "no ledger
     activity yet" invites the operator to go and run the pipeline, which is
     the wrong advice while the first fetch is simply still in flight.
     status="loading" drives BarChart's own shimmer skeleton -- gridlines stay
     put while the bars grow in, so the chart's frame does not move when the
     real data lands. */
  if (loading) {
    return (
      <>
        <LoadingAnnounce what="the daily timeline" />
        <BarChart
          data={[]}
          status="loading"
          xDataKey="label"
          aspectRatio="16 / 7"
          margin={{ left: 52, right: 6, top: 10, bottom: 4 }}
        >
          <Grid horizontal numTicksRows={5} strokeDasharray="" />
        </BarChart>
      </>
    )
  }

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
          /* The chart refuses pointer interaction until its entrance finishes
             (canInteract = isLoaded, set after animationDuration plus a 40%
             stagger). At the 1100ms default that is a ~1.54s window where
             hovering a column does nothing at all, which reads as a dead
             chart. 520ms puts it near 730ms, and a shorter grow suits a panel
             that repolls every 15 seconds anyway. */
          animationDuration={520}
          margin={{ left: 52, right: 6, top: 10, bottom: 4 }}
        >
          {/* Solid hairlines -- dashes read as "threshold" when this is just a scale. */}
          <Grid horizontal numTicksRows={5} strokeDasharray="" />
          <ValueAxis />
          {/* Reconciled sits underneath as context; outstanding stacks on top so
              the actionable share is the part that reads against the gridlines. */}
          {/* chart-context is a dark charcoal already close to the card
              background -- the default 0.3 fadedOpacity crushes it to near
              invisibility when the other series is hovered/isolated, which
              reads as a broken chart (floating bars with nothing beneath
              them) rather than a de-emphasized one. */}
          <Bar dataKey="reconciled_paise" fadedOpacity={0.7} fill="var(--chart-context)" lineCap={0} />
          <Bar dataKey="outstanding_paise" fill="var(--chart-bar)" lineCap="round" />

          {/* Until now this chart had no tooltip at all: hovering a column
              faded the other series and revealed no figures, so the only way
              to read a day's value was to eyeball it against the axis.

              `rows` is not optional in practice. The default renderer labels
              each row with its dataKey and formats with intFmt, which would
              print "outstanding_paise / 1750000" -- a debug readout on a
              money product. Order matches the stack, top segment first, so
              the tooltip reads in the same order as the column it describes.

              showDatePill is off deliberately: the pill repeats the title the
              tooltip already carries, and it is a scrubbing affordance for
              dense line charts rather than ~30 discrete columns. */}
          <ChartTooltip
            showDatePill={false}
            dotVariant="dot"
            dotSize={7}
            panelStyle={{
              border: '1px solid var(--border-strong)',
              boxShadow: 'var(--shadow)',
            }}
            rows={(point) => {
              const outstanding = Number(point.outstanding_paise ?? 0)
              const reconciled = Number(point.reconciled_paise ?? 0)
              const count = Number(point.count ?? 0)
              return [
                {
                  color: 'var(--chart-bar)',
                  label: 'Still outstanding',
                  value: fmtPaise(outstanding),
                },
                {
                  color: 'var(--chart-context)',
                  label: 'Reconciled',
                  value: fmtPaise(reconciled),
                },
                {
                  color: 'transparent',
                  label: 'Ledger value',
                  value: fmtPaise(outstanding + reconciled),
                },
                {
                  color: 'transparent',
                  label: count === 1 ? 'Payout' : 'Payouts',
                  value: count,
                },
              ]
            }}
          />
          {/* Thinned rather than allowed to collide -- every day is still named
              in the accessible table below. */}
          <BarXAxis maxLabels={10} />
        </BarChart>
      </ChartLegendHoverProvider>

      <SeriesLegend
        items={legendFor(days)}
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
