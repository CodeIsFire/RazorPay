import { LoadFailed } from '@/components/LoadFailed'
import { FlowSkeleton, GapHeroSkeleton, LogSkeleton } from '@/components/patterns/skeletons'
import { useDelayedFlag } from '@/hooks/useDelayedFlag'
import { GapHero } from '@/components/overview/GapHero'
import { ReconciliationFlow } from '@/components/overview/ReconciliationFlow'
import { RecentActivity } from '@/components/overview/RecentActivity'
import { useToast } from '@/components/Toast'
import { useAudit, useDaily, useFunnel, useIntegrationStatus } from '@/lib/queries'
import type { Navigate } from '@/App'

function ModeNotice() {
  const { data } = useIntegrationStatus()
  const live = data?.executor === 'live'

  /* The pill stays neutral until the executor is actually known. `data` is
     undefined on first paint, and reading that as "not live" labelled the
     banner "Test" before anything had been asked -- on an account routing real
     test-mode payouts through the Payouts API, that is a claim about where
     money goes, made from an unanswered request. Same rule the rest of this
     dashboard follows: unknown is its own state, not a falsy one. */
  const pill = !data ? 'pill' : live ? 'pill pill-green' : 'pill pill-orange'
  const mode = !data ? 'Checking' : live ? 'Live' : 'Test'

  return (
    <div className="notice">
      <span className={pill}>{mode}</span>
      <span
        title={
          data
            ? `key configured: ${data.key_configured} · account: ${data.account_number_configured} · webhook secret: ${data.webhook_secret_configured} · payable ledger rows: ${data.payable_ledger_rows}/${data.ledger_rows}`
            : undefined
        }
      >
        {!data
          ? 'Checking which executor payouts run through…'
          : live
            ? 'Routing dispatches through the RazorpayX Payouts API — on test-mode keys, moving no real money.'
            : 'Payouts are simulated in-process. Add RazorpayX credentials to dispatch through the Payouts API.'}
      </span>
      <a
        className="doc-link"
        href="https://razorpay.com/docs/api/x/"
        target="_blank"
        rel="noopener"
      >
        Documentation
        <svg viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M5.5 2.5H11.5V8.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M11.5 2.5L6 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          <path d="M9.5 11.5h-7v-7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </a>
    </div>
  )
}

export function OverviewTab({ onNavigate }: { onNavigate: Navigate }) {
  const funnel = useFunnel()
  const daily = useDaily()
  const audit = useAudit()
  const toast = useToast()

  /* isPending, never isFetching. isPending is true only while there is no data
     at all, so it goes false after the first success and stays false -- which
     means the 15s poll can never flash a skeleton over figures the operator is
     already reading. isFetching would strobe the whole page four times a
     minute. useDelayedFlag then suppresses the skeleton entirely for responses
     fast enough that showing one would just be a flicker. */
  const totalsLoading = useDelayedFlag(funnel.isPending || daily.isPending)
  const funnelLoading = useDelayedFlag(funnel.isPending)
  const auditLoading = useDelayedFlag(audit.isPending)

  async function refresh() {
    // refetch() reports failure in its result rather than by throwing, so
    // these have to be inspected -- otherwise a refresh that fetched nothing
    // still says "refreshed".
    const results = await Promise.all([funnel.refetch(), daily.refetch(), audit.refetch()])
    const failed = results.some((r) => r.isError)
    toast(failed ? 'Couldn’t refresh the overview.' : 'Overview refreshed.', failed)
  }

  return (
    <>
      <p className="page-desc">
        Reconciles the payout ledger against RazorpayX test-mode transactions and a bank
        statement, then works the difference over the Payouts API.
      </p>

      {/* Ordering is error -> loading -> data, and it matters in that order.
          The hero renders '–' for missing data, which reads as a real zero --
          "nothing is unreconciled" is the opposite of both "we could not ask"
          and "we have not asked yet", so each gets its own treatment. */}
      {funnel.isError || daily.isError ? (
        <LoadFailed
          what="the reconciliation totals"
          error={funnel.error ?? daily.error}
          onRetry={() => {
            void funnel.refetch()
            void daily.refetch()
          }}
          retrying={funnel.isFetching || daily.isFetching}
        />
      ) : totalsLoading ? (
        <GapHeroSkeleton />
      ) : (
        <GapHero
          funnel={funnel.data}
          days={daily.data?.days ?? []}
          onOpenExceptions={() => onNavigate('exceptions')}
        />
      )}

      <ModeNotice />

      {/* Replaces the old "Reconciliation summary" card, which spent a
          full-width panel on two key-value rows. Match rate and amount
          resolved are still here -- they moved into the flow's footer, beside
          the picture they are derived from, instead of standing alone. */}
      {funnel.isError ? (
        <LoadFailed
          what="the reconciliation summary"
          error={funnel.error}
          onRetry={() => funnel.refetch()}
          retrying={funnel.isFetching}
        />
      ) : funnelLoading ? (
        <FlowSkeleton />
      ) : (
        <ReconciliationFlow
          funnel={funnel.data}
          onOpenExceptions={() => onNavigate('exceptions')}
          onRefresh={refresh}
        />
      )}

      {/* Same trap as the Activity log tab: an empty stream here reads as
          "the pipeline has not run", which is an invitation to run it. */}
      {audit.isError ? (
        <LoadFailed
          what="recent activity"
          error={audit.error}
          onRetry={() => audit.refetch()}
          retrying={audit.isFetching}
        />
      ) : auditLoading ? (
        <div className="card">
          <div className="card-head">
            <div className="title">Recent activity</div>
          </div>
          <div className="card-body flush">
            <LogSkeleton />
          </div>
        </div>
      ) : (
        <RecentActivity entries={audit.data?.entries ?? []} onViewAll={() => onNavigate('audit')} />
      )}
    </>
  )
}
