import { IconRefresh } from '@/components/icons'
import { LoadFailed } from '@/components/LoadFailed'
import { GapHero } from '@/components/overview/GapHero'
import { RecentActivity } from '@/components/overview/RecentActivity'
import { useToast } from '@/components/Toast'
import { fmtPaise, fmtPct } from '@/lib/format'
import type { TabId } from '@/lib/labels'
import { useAudit, useDaily, useFunnel, useIntegrationStatus } from '@/lib/queries'

function ModeNotice() {
  const { data } = useIntegrationStatus()
  const live = data?.executor === 'live'

  return (
    <div className="notice">
      <span className={`pill ${live ? 'pill-green' : 'pill-orange'}`}>{live ? 'Live' : 'Test'}</span>
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

export function OverviewTab({ onNavigate }: { onNavigate: (tab: TabId) => void }) {
  const funnel = useFunnel()
  const daily = useDaily()
  const audit = useAudit()
  const toast = useToast()

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

      {/* The hero renders '–' for missing data, which reads as a real zero --
          "nothing is unreconciled" is the opposite of "we could not ask". */}
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
      ) : (
        <GapHero
          funnel={funnel.data}
          days={daily.data?.days ?? []}
          onOpenExceptions={() => onNavigate('exceptions')}
        />
      )}

      <ModeNotice />

      <div className="card">
        <div className="card-head">
          <div>
            <div className="title">Reconciliation summary</div>
          </div>
          <button className="btn icon-btn" title="Refresh" aria-label="Refresh" onClick={refresh}>
            <IconRefresh />
          </button>
        </div>
        <div className="card-body">
          {funnel.isError && (
            <LoadFailed
              what="the reconciliation summary"
              error={funnel.error}
              onRetry={() => funnel.refetch()}
              retrying={funnel.isFetching}
            />
          )}
          <div className="kv-row">
            <div className="k">Match rate</div>
            <div className="v">
              {fmtPct(funnel.data?.match_rate)}
              <span className="note">
                Reconciled ÷ transactions — how much of the ledger settled automatically, with
                nothing raised.
              </span>
            </div>
          </div>
          <div className="kv-row">
            <div className="k">Amount resolved</div>
            <div className="v">
              {funnel.data ? fmtPaise(funnel.data.amount_recovered_paise) : '–'}
              <span className="note">
                Sum of resolved ledger-side records — fee disputes closed, payouts retried,
                pending updates confirmed.
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Same trap as the Activity log tab: an empty stream here reads as
          "the pipeline has not run", which is an invitation to run it. */}
      {audit.isError ? (
        <LoadFailed
          what="recent activity"
          error={audit.error}
          onRetry={() => audit.refetch()}
          retrying={audit.isFetching}
        />
      ) : (
        <RecentActivity entries={audit.data?.entries ?? []} onViewAll={() => onNavigate('audit')} />
      )}
    </>
  )
}
