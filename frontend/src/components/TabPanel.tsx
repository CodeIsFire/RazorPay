import { motion, useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'
import type { TabId } from '@/lib/labels'
import { TAB_TITLES } from '@/lib/labels'

/* A fade-in on the incoming panel, and deliberately nothing more.

   This does NOT use AnimatePresence. With it, exiting panels were marked
   `hidden` and never unmounted: all four tabs accumulated in the DOM, each
   keeping its queries polling and its ResizeObservers attached. The legacy
   stylesheet's `.tab-panel[hidden] { display: none }` hid the wreckage, so it
   looked correct while leaking a whole tab's worth of work per visit.

   Keying a plain motion.section on the tab makes React unmount the old panel
   the moment the new one arrives -- one tabpanel in the DOM, which is also
   what the aria-controls on both nav surfaces claims. The cost is that the
   outgoing panel does not fade out, which at 180ms nobody can see.

   Under prefers-reduced-motion the panel just appears. The stylesheet already
   collapses CSS transitions globally, but Motion animates via JS and would
   ignore that -- so the duration is zeroed here too. */
export function TabPanel({ tab, children }: { tab: TabId; children: ReactNode }) {
  const reduced = useReducedMotion()

  return (
    <motion.section
      key={tab}
      className="tab-panel"
      id={`tab-${tab}`}
      role="tabpanel"
      aria-label={TAB_TITLES[tab]}
      tabIndex={0}
      initial={{ opacity: 0, y: reduced ? 0 : 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.18, ease: [0.22, 0.61, 0.36, 1] }}
    >
      {children}
    </motion.section>
  )
}
