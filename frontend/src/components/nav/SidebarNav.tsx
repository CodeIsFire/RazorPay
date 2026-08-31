import { useTablistKeys } from '@/hooks/useTablistKeys'
import { TAB_IDS, TAB_TITLES, type TabId } from '@/lib/labels'

/** The labelled twin of the icon rail, driving the same panel region. Below
    900px the rail is hidden and this becomes a horizontal scroller. */
export function SidebarNav({
  activeTab,
  onSelect,
}: {
  activeTab: TabId
  onSelect: (tab: TabId) => void
}) {
  const onKeyDown = useTablistKeys(onSelect)

  return (
    <div className="sidebar" role="tablist" aria-label="Sections" onKeyDown={onKeyDown}>
      <div className="sidebar-title">Reconciliation</div>
      {TAB_IDS.map((tab) => {
        const active = tab === activeTab
        return (
          <button
            key={tab}
            className={`sidebar-link${active ? ' active' : ''}`}
            data-tab={tab}
            role="tab"
            aria-selected={active}
            // Only while selected -- the other panels are not mounted, and
            // aria-controls must reference a real element. See RailNav.
            aria-controls={active ? `tab-${tab}` : undefined}
            tabIndex={active ? 0 : -1}
            onClick={() => onSelect(tab)}
          >
            {TAB_TITLES[tab]}
          </button>
        )
      })}
    </div>
  )
}
