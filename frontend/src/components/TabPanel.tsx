import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'
import type { TabId } from '@/lib/labels'
import { TAB_TITLES } from '@/lib/labels'

/* A cross-fade, and deliberately nothing more: tabs here swap dense tables and
   charts, and sliding several hundred rows sideways draws attention to the
   transition rather than to the data that arrived. mode="wait" keeps the
   outgoing panel from overlapping the incoming one, which would otherwise
   double the page height mid-swap and jump the scrollbar.

   Under prefers-reduced-motion the panel just appears. The stylesheet already
   collapses CSS transitions globally, but Motion animates via JS and would
   ignore that -- so the duration is zeroed here too. */
export function TabPanel({ tab, children }: { tab: TabId; children: ReactNode }) {
  const reduced = useReducedMotion()

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.section
        key={tab}
        className="tab-panel"
        id={`tab-${tab}`}
        role="tabpanel"
        aria-label={TAB_TITLES[tab]}
        tabIndex={0}
        initial={{ opacity: 0, y: reduced ? 0 : 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: reduced ? 0 : 0.18, ease: [0.22, 0.61, 0.36, 1] }}
      >
        {children}
      </motion.section>
    </AnimatePresence>
  )
}
