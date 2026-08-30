import { IconRefresh } from '@/components/icons'
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
    await Promise.all([funnel.refetch(), daily.refetch(), audit.refetch()])
    toast('Overview refreshed.')
  }

  return (
    <>
      <p className="page-desc">
        Reconciles the payout ledger against RazorpayX test-mode transactions and a bank
        statement, then works the difference over the Payouts API.
      </p>

      <GapHero
        funnel={funnel.data}
        days={daily.data?.days ?? []}
        onOpenExceptions={() => onNavigate('exceptions')}
      />

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

      <RecentActivity entries={audit.data?.entries ?? []} onViewAll={() => onNavigate('audit')} />
    </>
  )
}
