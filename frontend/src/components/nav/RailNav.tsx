import { IconAudit, IconExceptions, IconInsights, IconOverview, IconSettings } from '@/components/icons'
import { useTablistKeys } from '@/hooks/useTablistKeys'
import { TAB_TITLES, type TabId } from '@/lib/labels'
import { useToast } from '@/components/Toast'

const RAIL_ICONS: Record<TabId, (p: { className?: string }) => React.ReactElement> = {
  overview: IconOverview,
  exceptions: IconExceptions,
  insights: IconInsights,
  audit: IconAudit,
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
              aria-controls={`tab-${tab}`}
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
    </aside>
  )
}
