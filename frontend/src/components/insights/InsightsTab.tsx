import { useMemo } from 'react'
import { BarRows, type BarRow } from '@/components/insights/BarRows'
import { DailyTimeline } from '@/components/insights/DailyTimeline'
import { IconRefresh } from '@/components/icons'
import { LoadFailed } from '@/components/LoadFailed'
import { BarRowsSkeleton, TileRowSkeleton } from '@/components/patterns/skeletons'
import { useToast } from '@/components/Toast'
import { useDelayedFlag } from '@/hooks/useDelayedFlag'
import { fmtPaise } from '@/lib/format'
import { groupBacklogByCause } from '@/lib/insights'
import { causeLabel, isKnownCause } from '@/lib/labels'
import { useAnalyticsExceptions, useDaily, useExceptions } from '@/lib/queries'
import type { Navigate } from '@/App'

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const recordTip = (name: string, amountPaise: number, count: number) =>
  `${name} — ${fmtPaise(amountPaise)} across ${plural(count, 'record')}`

/* Backlog-by-cause carries a share of total value alongside the count, which
   the other two charts do not: it is the only one whose rows partition a
   single whole, so "25% of value" is a real reading there and would be
   meaningless on an age bucket or a top-8 list. */
const causePct = (share: number) => `${(share * 100).toFixed(0)}% of value`

const EMPTY = 'Nothing in the backlog — everything reconciled.'

export function InsightsTab({ onNavigate }: { onNavigate: Navigate }) {
  const analytics = useAnalyticsExceptions()
  const daily = useDaily()
  const exceptions = useExceptions()
  const toast = useToast()

  const a = analytics.data
  // The tiles and all three charts come from these two queries, so they load
  // as one page rather than popping in independently.
  const statsLoading = useDelayedFlag(analytics.isPending)
  const dailyLoading = useDelayedFlag(daily.isPending)
  const records = useMemo(
    () => groupBacklogByCause(exceptions.data?.entries ?? []),
    [exceptions.data],
  )

  const pastBound = a?.by_age.find((b) => b.past_bound) ?? { count: 0, amount_paise: 0 }

  const causeRows: BarRow[] = (a?.by_cause ?? []).map((c) => ({
    key: c.cause,
    label: causeLabel(c.cause),
    amountPaise: c.amount_paise,
    value: fmtPaise(c.amount_paise),
    sub: `${c.count} · ${causePct(c.share)}`,
    title: recordTip(causeLabel(c.cause), c.amount_paise, c.count),
  }))

  const ageRows: BarRow[] = (a?.by_age ?? []).map((b) => ({
    key: b.bucket,
    label: b.bucket,
    amountPaise: b.amount_paise,
    value: fmtPaise(b.amount_paise),
    sub: plural(b.count, 'record'),
    // A bucket can be past the bound with nothing in it; colouring an empty
    // bar red would raise an alarm about no money at all.
    alarm: b.past_bound && b.count > 0,
    title: recordTip(b.bucket, b.amount_paise, b.count),
  }))

  const counterpartyRows: BarRow[] = (a?.top_counterparties ?? []).map((c) => ({
    key: c.counterparty,
    label: c.counterparty,
    amountPaise: c.amount_paise,
    value: fmtPaise(c.amount_paise),
    sub: plural(c.count, 'record'),
    title: recordTip(c.counterparty, c.amount_paise, c.count),
  }))

  return (
    <>
      <p className="page-desc">
        Where the unreconciled money actually sits. Ages are measured from when the payout
        occurred, not from when the pipeline last ran.
      </p>

      {/* One banner for the tab rather than one per chart: all three charts
          and all three tiles come from the same two queries, so a failure is
          a property of the page, not of any one card. The tiles below keep
          showing '–', which the banner explains is "unknown", not "zero". */}
      {(analytics.isError || daily.isError || exceptions.isError) && (
        <LoadFailed
          what="the backlog analytics"
          error={analytics.error ?? daily.error ?? exceptions.error}
          onRetry={() => {
            void analytics.refetch()
            void daily.refetch()
            void exceptions.refetch()
          }}
          retrying={analytics.isFetching || daily.isFetching || exceptions.isFetching}
        />
      )}

      {statsLoading ? (
        <TileRowSkeleton />
      ) : (
      <div className="kpi-row">
        <div className="tile">
          <div className="label">Value at risk</div>
          <div className="value">{a ? fmtPaise(a.value_at_risk_paise) : '–'}</div>
        </div>
        <div className="tile">
          <div className="label">Records in backlog</div>
          <div className="value">{a ? a.exception_count : '–'}</div>
        </div>
        <button
          className="tile clickable"
          onClick={() => onNavigate('exceptions')}
          title={
            pastBound.count
              ? `${fmtPaise(pastBound.amount_paise)} has been unreconciled longer than the router's ${a?.max_exception_age_days}-day retry window`
              : 'Nothing has aged past the retry window'
          }
        >
          <div className="label">
            {a ? `Older than ${a.max_exception_age_days} days` : 'Past the retry window'}
          </div>
          <div className={`value${pastBound.count > 0 ? ' attention' : ''}`}>
            {a ? pastBound.count : '–'}
          </div>
        </button>
      </div>
      )}

      <div className="card">
        <div className="card-head">
          <div>
            <div className="title">Backlog by cause</div>
            <div className="sub">
              Ranked by money held up, not by how many records there are — six small duplicates
              matter less than one large failed payout.
            </div>
          </div>
          <button
            className="btn icon-btn"
            title="Refresh"
            aria-label="Refresh"
            onClick={async () => {
              const results = await Promise.all([
                analytics.refetch(),
                daily.refetch(),
                exceptions.refetch(),
              ])
              const failed = results.some((r) => r.isError)
              toast(failed ? 'Couldn’t refresh insights.' : 'Insights refreshed.', failed)
            }}
          >
            <IconRefresh />
          </button>
        </div>
        <div className="card-body">
          {statsLoading ? (
            <BarRowsSkeleton />
          ) : (
            /* Only this chart's rows drill through. A cause maps one-to-one
               onto the exceptions table's own causeFilter, so the destination
               genuinely shows the records the bar was drawn from. The other two
               charts deliberately do not: age is derived from the transaction's
               occurred_at, which is not on an exception row at all, and
               counterparty lives on transactions rather than exceptions -- a
               filter for either would land on an empty table and read as "these
               records vanished". */
            <BarRows
              rows={causeRows}
              empty={EMPTY}
              recordsByCause={records}
              buckets={a?.by_cause}
              onSelect={(cause) =>
                onNavigate('exceptions', isKnownCause(cause) ? { cause } : undefined)
              }
              selectHint={(row) => `Show the ${row.label.toLowerCase()} records`}
            />
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <div className="title">Where the stuck money came from</div>
            <div className="sub">
              Ledger value per business day. The lit portion is what still hasn't reconciled — it
              shows which days' payouts are actually holding things up.
            </div>
          </div>
        </div>
        <div className="card-body">
          {/* The legend moved inside the chart: it drives which series is
              emphasised, so it has to share that state with the bars. */}
          <DailyTimeline days={daily.data?.days ?? []} loading={dailyLoading} />
        </div>
      </div>

      <div className="insights-columns">
        <div className="card">
          <div className="card-head">
            <div>
              <div className="title">How long it has been sitting</div>
              <div className="sub">
                Time since the payout occurred.
                {a && ` The router stops retrying after ${a.max_exception_age_days} days.`}
              </div>
            </div>
          </div>
          <div className="card-body">
            {statsLoading ? <BarRowsSkeleton rows={4} /> : <BarRows rows={ageRows} empty={EMPTY} />}
            <div className="chart-legend">
              <span className="item">
                <span className="swatch" style={{ background: 'var(--chart-bar)' }} />
                Within the retry window
              </span>
              <span className="item">
                <span className="swatch" style={{ background: 'var(--chart-alarm)' }} />
                Past it — the router abandons these
              </span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <div className="title">Counterparties to chase</div>
              <div className="sub">Payees with the most money held up.</div>
            </div>
          </div>
          <div className="card-body">
            {statsLoading ? (
              <BarRowsSkeleton rows={6} />
            ) : (
              <BarRows rows={counterpartyRows} empty={EMPTY} />
            )}
          </div>
        </div>
      </div>
    </>
  )
}
