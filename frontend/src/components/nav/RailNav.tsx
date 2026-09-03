import { useState } from 'react'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import {
  IconAudit,
  IconData,
  IconExceptions,
  IconInsights,
  IconOverview,
  IconRefresh,
  IconSettings,
} from '@/components/icons'
import { useTablistKeys } from '@/hooks/useTablistKeys'
import { TAB_TITLES, type TabId } from '@/lib/labels'
import { useToast } from '@/components/Toast'
import { ApiError } from '@/lib/api'
import { useResetDemo } from '@/lib/queries'

const RAIL_ICONS: Record<TabId, (p: { className?: string }) => React.ReactElement> = {
  overview: IconOverview,
  exceptions: IconExceptions,
  insights: IconInsights,
  audit: IconAudit,
  data: IconData,
}

const DEV_CONTROLS_BLURB =
  'Executor, keys and webhook wiring are reported by GET /integration/status.'

export function RailNav({
  activeTab,
  onSelect,
}: {
  activeTab: TabId
  onSelect: (tab: TabId) => void
}) {
  const onKeyDown = useTablistKeys(onSelect)
  const toast = useToast()
  const reset = useResetDemo()
  const [confirmingReset, setConfirmingReset] = useState(false)

  async function runReset() {
    setConfirmingReset(false)
    toast('Reloading demo data…')
    try {
      const r = await reset.mutateAsync(undefined)
      toast(
        `Demo data reloaded — ${r.ledger} ledger rows, ${r.bank_statement} bank rows, ` +
          `${r.exceptions} records needing attention`,
      )
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Couldn’t reload the demo data', true)
    }
  }

  return (
    <aside className="rail">
      <div className="logo-mark" title="Reconcile → Recover">
        ⇄
      </div>
      <nav
        className="rail-nav"
        role="tablist"
        aria-label="Sections"
        aria-orientation="vertical"
        onKeyDown={onKeyDown}
      >
        {(Object.keys(RAIL_ICONS) as TabId[]).map((tab) => {
          const Icon = RAIL_ICONS[tab]
          const active = tab === activeTab
          return (
            <button
              key={tab}
              className={`rail-item${active ? ' active' : ''}`}
              data-tab={tab}
              role="tab"
              aria-selected={active}
              /* Only the selected tab points at a panel. TabPanel renders one
                 panel at a time on purpose -- unmounting the outgoing tab is
                 what stops background tabs polling -- so the other three ids
                 do not exist in the document, and aria-controls is required to
                 reference an element that does. Pointing at nothing is worse
                 than not pointing. */
              aria-controls={active ? `tab-${tab}` : undefined}
              // Only the focused tab is in the tab order; arrows move between
              // them. Without this every rail item is its own tab stop.
              tabIndex={active ? 0 : -1}
              title={TAB_TITLES[tab]}
              aria-label={TAB_TITLES[tab]}
              onClick={() => onSelect(tab)}
            >
              <Icon />
            </button>
          )
        })}
      </nav>
      <div className="rail-spacer" />

      {/* Outside the tablist: it is an action, not a destination, and putting
          it inside would make the arrow keys land on something that opens a
          destructive dialog instead of switching tab. */}
      <button
        className="rail-item"
        title="Reload demo data"
        aria-label="Reload demo data"
        disabled={reset.isPending}
        onClick={() => setConfirmingReset(true)}
      >
        <IconRefresh />
      </button>

      {/* Reports where to look rather than calling anything: the live wiring
          is read by the integration-status query that drives the mode chip. */}
      <button
        className="rail-item"
        title="Developer controls"
        aria-label="Developer controls"
        onClick={() => toast(DEV_CONTROLS_BLURB)}
      >
        <IconSettings />
      </button>

      {confirmingReset && (
        <ConfirmDialog
          title="Reload demo data?"
          titleId="reset-demo-title"
          confirmLabel={reset.isPending ? 'Reloading…' : 'Reload demo data'}
          confirmDisabled={reset.isPending}
          onConfirm={runReset}
          onCancel={() => setConfirmingReset(false)}
        >
          <div className="modal-body">
            <p>
              This clears <b>everything</b> currently loaded — including any ledger or bank
              statement you uploaded — and reloads the built-in demo dataset, reconciled and
              ready. It can’t be undone from here.
            </p>
            <p>
              Nothing is dispatched: this reloads and reconciles only, and never moves money.
            </p>
          </div>
        </ConfirmDialog>
      )}
    </aside>
  )
}
