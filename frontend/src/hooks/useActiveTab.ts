import { useCallback, useEffect, useState } from 'react'
import { TAB_IDS, type TabId } from '@/lib/labels'

function tabFromHash(): TabId {
  const raw = (window.location.hash || '').replace('#', '') as TabId
  return TAB_IDS.includes(raw) ? raw : 'overview'
}

/* Four fixed tabs deep-linked by hash. replaceState rather than pushState, so
   clicking through tabs doesn't fill the back button with dashboard states --
   Back should leave the dashboard, which is how the vanilla version behaved.
   The hashchange listener is what makes the browser's own Back/Forward still
   work if the user does edit the hash. */
export function useActiveTab(): [TabId, (tab: TabId) => void] {
  const [activeTab, setActiveTab] = useState<TabId>(tabFromHash)

  useEffect(() => {
    const onHashChange = () => setActiveTab(tabFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const selectTab = useCallback((tab: TabId) => {
    setActiveTab(tab)
    window.history.replaceState(null, '', `#${tab}`)
  }, [])

  return [activeTab, selectTab]
}
