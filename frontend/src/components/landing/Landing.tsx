import type { TabId } from '@/lib/labels'

/** The entry screen, shown on a bare URL.

    Assigns window.location.hash rather than calling useActiveTab's
    selectTab: that helper uses replaceState, which is right for switching
    tabs (Back should leave the dashboard, not walk back through every tab
    someone visited) and wrong for entering, where it would make the very
    first click cost you the landing. Assigning the hash pushes one history
    entry and fires hashchange, which is what useEntered reads. */
function enter(tab: TabId) {
  window.location.hash = tab
}

export function Landing() {
  return (
    <main className="landing">
      <div className="landing-inner">
        <div className="landing-mark" aria-hidden="true">
          ⇄
        </div>
        <h1 className="landing-title">Reconcile → Recover</h1>
        <p className="landing-sub">
          Match the payout ledger against what RazorpayX and the bank actually did, then work
          the difference over the Payouts API.
        </p>
        <div className="landing-actions">
          <button className="btn btn-primary" onClick={() => enter('overview')}>
            Open dashboard
          </button>
          <button className="btn" onClick={() => enter('data')}>
            Try it yourself
          </button>
        </div>
        <p className="landing-note">
          “Try it yourself” goes to the Data tab, where you can upload your own ledger and bank
          statement as CSV.
        </p>
      </div>
    </main>
  )
}
