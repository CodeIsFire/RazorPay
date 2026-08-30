import { IconSearch } from '@/components/icons'
import { RunMenu } from '@/components/RunMenu'
import { SEARCH_PLACEHOLDERS, TAB_TITLES, type TabId } from '@/lib/labels'

export function TopBar({
  activeTab,
  query,
  onQueryChange,
}: {
  activeTab: TabId
  query: string
  onQueryChange: (q: string) => void
}) {
  const placeholder = SEARCH_PLACEHOLDERS[activeTab]

  return (
    <header className="topbar">
      <h1 id="page-title">{TAB_TITLES[activeTab]}</h1>
      <div className="topbar-right">
        {/* One search box serves whichever table is on screen; Overview and
            Insights have no table, so the control is hidden there rather than
            left inert. */}
        <div className="search-wrap" hidden={!placeholder}>
          <IconSearch />
          <input
            id="global-search"
            type="search"
            value={query}
            placeholder={placeholder ?? 'Search'}
            aria-label="Search"
            onChange={(e) => onQueryChange(e.target.value)}
          />
        </div>
        <RunMenu />
        <div className="avatar" title="Signed in">
          RR
        </div>
      </div>
    </header>
  )
}
