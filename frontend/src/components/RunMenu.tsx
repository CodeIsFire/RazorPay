import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { IconChevron } from '@/components/icons'
import { RouteConfirm } from '@/components/RouteConfirm'
import { useToast } from '@/components/Toast'
import { useDismissableMenu } from '@/hooks/useDismissableMenu'
import { api, ApiError } from '@/lib/api'
import { useReconcile, useRoute, useSyncPayouts } from '@/lib/queries'

/** Reconciling the bank statement is a second pass over the same ledger, not
    a different pipeline -- both runs are offered because a full picture needs
    both, and the demo loader depends on that. */
export function RunMenu() {
  const { open, setOpen, close, triggerRef, panelRef } = useDismissableMenu<
    HTMLButtonElement,
    HTMLDivElement
  >()
  const toast = useToast()
  const reconcile = useReconcile()
  const route = useRoute()
  const syncPayouts = useSyncPayouts()

  const busy = reconcile.isPending || route.isPending || syncPayouts.isPending

  /* Route is the one action here that moves money, so it goes through a
     confirmation carrying the real dispatch count. The preview is fetched only
     while the dialog is open (`enabled`), and never cached -- a stale count on
     a "dispatch N payouts" button is worse than no count, because it is the
     number the operator is agreeing to. */
  const [confirmingRoute, setConfirmingRoute] = useState(false)
  const routePreview = useQuery({
    queryKey: ['route-preview'],
    queryFn: api.routePreview,
    enabled: confirmingRoute,
    gcTime: 0,
    staleTime: 0,
  })

  async function run(label: string, fn: () => Promise<unknown>, describe: (r: never) => string) {
    close(false)
    toast(`${label}…`)
    try {
      const result = await fn()
      toast(describe(result as never))
    } catch (err) {
      toast(err instanceof ApiError ? err.message : `${label} failed`, true)
    }
  }

  return (
    <div className="menu-wrap">
      <button
        ref={triggerRef}
        id="btn-run-menu"
        aria-haspopup="true"
        aria-expanded={open}
        disabled={busy}
        onClick={() => setOpen(!open)}
      >
        {busy ? 'Running…' : 'Run'}
        <IconChevron />
      </button>
      <div ref={panelRef} className="menu" hidden={!open} role="menu">
        <button
          role="menuitem"
          onClick={() =>
            run('Running reconcile', () => reconcile.mutateAsync('gateway'), (r: { count: number }) =>
              r.count === 0
                ? 'Reconcile finished — nothing new needs attention'
                : `Reconcile finished — ${r.count} new record${r.count === 1 ? '' : 's'} need attention`,
            )
          }
        >
          Run reconcile
          <span className="hint">Match the ledger, then classify what didn't</span>
        </button>
        <button
          role="menuitem"
          onClick={() => {
            close(false)
            setConfirmingRoute(true)
          }}
        >
          Run route
          <span className="hint">Dispatch actions for anything needing attention</span>
        </button>
        <button
          role="menuitem"
          onClick={() =>
            run(
              'Syncing payout status',
              () => syncPayouts.mutateAsync(undefined),
              (r: { checked: number; confirmed: number }) =>
                `Checked ${r.checked} payout${r.checked === 1 ? '' : 's'} — ${r.confirmed} settled`,
            )
          }
        >
          Sync payout status
          <span className="hint">
            Ask RazorpayX about payouts still in flight, in case a webhook was missed
          </span>
        </button>
      </div>

      {confirmingRoute && (
        <RouteConfirm
          preview={routePreview.data ?? null}
          loading={routePreview.isLoading}
          error={routePreview.error}
          confirming={route.isPending}
          onCancel={() => setConfirmingRoute(false)}
          onConfirm={async () => {
            setConfirmingRoute(false)
            await run(
              'Running route',
              () => route.mutateAsync(undefined),
              (r: { dispatched: number; skipped: number }) =>
                `Route finished — ${r.dispatched} dispatched, ${r.skipped} held`,
            )
          }}
        />
      )}
    </div>
  )
}
