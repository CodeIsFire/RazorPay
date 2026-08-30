import { IconChevron } from '@/components/icons'
import { useToast } from '@/components/Toast'
import { useDismissableMenu } from '@/hooks/useDismissableMenu'
import { ApiError } from '@/lib/api'
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
          onClick={() =>
            run('Running route', () => route.mutateAsync(undefined), (r: { dispatched: number; skipped: number }) =>
              `Route finished — ${r.dispatched} dispatched, ${r.skipped} held`,
            )
          }
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
    </div>
  )
}
