import { OpenPanel } from '@openpanel/sdk'
import type { TabId } from './labels'

/* The only place in the app that talks to an analytics service.

   This dashboard renders bank references and can dispatch payouts, so the
   integration is deliberately much narrower than OpenPanel's default install:

   - It uses `@openpanel/sdk`, not `@openpanel/web`. The web package depends on
     rrweb (session replay), which records the DOM -- here that would stream
     ledger references, counterparty names and amounts into replays. The base
     SDK has zero dependencies and only sends what you hand it.
   - It is bundled, not loaded from openpanel.dev. No third-party origin serves
     executable code to a page that can move money. (The vanilla dashboard
     vendored d3 and Lenis same-origin for exactly this reason.)
   - None of OpenPanel's auto-instrumentation is enabled -- no screen views, no
     outgoing links, no attribute tracking. Every one of those reads the DOM.
     We emit one event, carrying one of four tab names we control.

   VITE_OPENPANEL_CLIENT_ID is inlined into the bundle at build time. That is
   correct for a client id -- it is public by design, like a GA measurement id.
   The SDK's `clientSecret` is never used here: a secret in a browser bundle
   would not be one. */

// Read lazily rather than at module scope so the value is observable at call
// time -- module-scope capture would freeze whatever was set when the module
// first loaded, which is exactly what a test cannot then vary.
const readClientId = () =>
  import.meta.env.VITE_OPENPANEL_CLIENT_ID as string | undefined

/** Unconfigured is the default, and it means silence -- a fresh clone, a dev
    run and CI all send nothing until someone deliberately sets an id. */
function optedOut(): boolean {
  if (!readClientId()) return true
  if (typeof navigator === 'undefined') return true
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean }
  return navigator.doNotTrack === '1' || nav.globalPrivacyControl === true
}

let client: OpenPanel | null = null
let resolved = false

function getClient(): OpenPanel | null {
  if (resolved) return client
  resolved = true
  if (optedOut()) return null
  try {
    client = new OpenPanel({ clientId: readClientId()! })
  } catch {
    // A analytics client that fails to construct must not take the dashboard
    // with it.
    client = null
  }
  return client
}

/** The one event this app sends. `tab` is typed to the four tab literals, so a
    ledger reference cannot reach OpenPanel through here even by mistake --
    there is no free-text parameter to pass one through. */
export function trackTabView(tab: TabId): void {
  const op = getClient()
  if (!op) return
  try {
    // Fire-and-forget. Nothing downstream waits on analytics, and a rejected
    // promise must never surface as an error toast on a tool that dispatches
    // payouts.
    void op.track('tab_view', { tab }).catch(() => {})
  } catch {
    /* ignore */
  }
}

/** Test seam only: clears the memoised client so a test can vary the env. */
export function __resetAnalyticsForTests(): void {
  client = null
  resolved = false
}
