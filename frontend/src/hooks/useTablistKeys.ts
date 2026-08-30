import { useCallback, type KeyboardEvent } from 'react'
import { TAB_IDS, type TabId } from '@/lib/labels'

const KEYS = ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End']

/* Roving arrow-key navigation inside one tablist, moving focus and switching
   tab together (the WAI-ARIA "automatic activation" pattern).

   Hand-rolled rather than taken from Radix Tabs: this app has TWO tab
   surfaces -- the icon rail and the labelled sidebar -- driving ONE panel
   region. Radix wants the panels inside its Root and generates the ids that
   link them, so two Roots would mean either duplicating every panel or
   pointing aria-controls at ids that don't exist. Owning these twenty lines
   keeps aria-controls pointed at the real panel. */
export function useTablistKeys(onSelect: (tab: TabId) => void) {
  return useCallback(
    (ev: KeyboardEvent<HTMLElement>) => {
      if (!KEYS.includes(ev.key)) return
      const list = ev.currentTarget
      const tabs = [...list.querySelectorAll<HTMLElement>('[role="tab"]')]
      const i = tabs.indexOf(document.activeElement as HTMLElement)
      if (i < 0) return
      ev.preventDefault()
      const next =
        ev.key === 'Home'
          ? 0
          : ev.key === 'End'
            ? tabs.length - 1
            : ev.key === 'ArrowRight' || ev.key === 'ArrowDown'
              ? (i + 1) % tabs.length
              : (i - 1 + tabs.length) % tabs.length
      tabs[next].focus()
      const tab = tabs[next].dataset.tab as TabId | undefined
      if (tab && TAB_IDS.includes(tab)) onSelect(tab)
    },
    [onSelect],
  )
}
