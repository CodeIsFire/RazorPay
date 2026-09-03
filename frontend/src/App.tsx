import { useEffect, useRef, useState } from 'react'
import { ChatPanel } from '@/components/chat/ChatPanel'
import { AuditTab } from '@/components/audit/AuditTab'
import { DataTab } from '@/components/data/DataTab'
import { Landing } from '@/components/landing/Landing'
import { ExceptionsTab } from '@/components/exceptions/ExceptionsTab'
import { InsightsTab } from '@/components/insights/InsightsTab'
import { OverviewTab } from '@/components/overview/OverviewTab'
import { RailNav } from '@/components/nav/RailNav'
import { SidebarNav } from '@/components/nav/SidebarNav'
import { TabPanel } from '@/components/TabPanel'
import { ToastProvider } from '@/components/Toast'
import { TopBar } from '@/components/TopBar'
import { UserCursor } from '@/components/cursor/UserCursor'
import { useActiveTab } from '@/hooks/useActiveTab'
import { useEntered } from '@/hooks/useEntered'
import { useLenis } from '@/hooks/useLenis'
import type { TabId } from '@/lib/labels'
import type { Cause } from '@/lib/types'

/** Tab navigation, optionally carrying what the destination should focus on.
    The options argument is how a chart says "these records, specifically"
    without every caller having to know how the exceptions filters are wired. */
export type Navigate = (tab: TabId, opts?: { cause?: Cause }) => void

function TabContent({
  tab,
  onNavigate,
  query,
  causeFocus,
}: {
  tab: TabId
  onNavigate: Navigate
  query: string
  causeFocus: Cause | null
}) {
  switch (tab) {
    case 'overview':
      return <OverviewTab onNavigate={onNavigate} />
    case 'exceptions':
      return <ExceptionsTab query={query} causeFocus={causeFocus} />
    case 'insights':
      return <InsightsTab onNavigate={onNavigate} />
    case 'audit':
      return <AuditTab query={query} />
    case 'data':
      return <DataTab />
  }
}

function Dashboard() {
  const [activeTab, selectTab] = useActiveTab()
  const [query, setQuery] = useState('')
  /* Set when a chart drills into the records behind one of its bars. It is
     read once, by ExceptionsTab's initial state -- TabPanel keys its panel on
     the tab id, so switching tabs genuinely remounts the incoming one and
     "initial state" means "every time you arrive". Navigating without a cause
     clears it, so a later plain visit to the tab is unfiltered. */
  const [causeFocus, setCauseFocus] = useState<Cause | null>(null)
  const contentRef = useRef<HTMLElement>(null)
  const contentInnerRef = useRef<HTMLDivElement>(null)

  useLenis(contentRef, contentInnerRef)

  const navigate: Navigate = (tab, opts) => {
    setCauseFocus(opts?.cause ?? null)
    selectTab(tab)
  }

  // One search box serves two different tables, so the term does not survive a
  // tab change: carrying "failed_payment" over to the activity log would
  // silently hide most of it with no visible cause.
  useEffect(() => setQuery(''), [activeTab])

  return (
    <>
      <div className="brand-rule" />
      <div className="app">
        <RailNav activeTab={activeTab} onSelect={navigate} />
        <SidebarNav activeTab={activeTab} onSelect={navigate} />
        <div className="main">
          <TopBar activeTab={activeTab} query={query} onQueryChange={setQuery} />
          <main className="content" ref={contentRef}>
            <div className="content-inner" ref={contentInnerRef}>
              <TabPanel tab={activeTab}>
                <TabContent
                  tab={activeTab}
                  onNavigate={navigate}
                  query={query}
                  causeFocus={causeFocus}
                />
              </TabPanel>
            </div>
          </main>
        </div>
      </div>
      <ChatPanel />
    </>
  )
}

export default function App() {
  /* Landing or dashboard, never both -- and never mounted together. That
     matters beyond tidiness: Dashboard is what calls useActiveTab, so while
     the landing is up no tab_view is reported to analytics and none of the
     15s polls are running. Someone who never enters costs nothing. */
  const entered = useEntered()

  return (
    <ToastProvider>
      {entered ? <Dashboard /> : <Landing />}
      <UserCursor />
    </ToastProvider>
  )
}
