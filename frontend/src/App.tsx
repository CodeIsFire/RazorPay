import { useEffect, useRef, useState } from 'react'
import { ChatPanel } from '@/components/chat/ChatPanel'
import { AuditTab } from '@/components/audit/AuditTab'
import { ExceptionsTab } from '@/components/exceptions/ExceptionsTab'
import { InsightsTab } from '@/components/insights/InsightsTab'
import { OverviewTab } from '@/components/overview/OverviewTab'
import { RailNav } from '@/components/nav/RailNav'
import { SidebarNav } from '@/components/nav/SidebarNav'
import { TabPanel } from '@/components/TabPanel'
import { ToastProvider } from '@/components/Toast'
import { TopBar } from '@/components/TopBar'
import { useActiveTab } from '@/hooks/useActiveTab'
import { useLenis } from '@/hooks/useLenis'
import type { TabId } from '@/lib/labels'

function TabContent({
  tab,
  onNavigate,
  query,
}: {
  tab: TabId
  onNavigate: (tab: TabId) => void
  query: string
}) {
  switch (tab) {
    case 'overview':
      return <OverviewTab onNavigate={onNavigate} />
    case 'exceptions':
      return <ExceptionsTab query={query} />
    case 'insights':
      return <InsightsTab onNavigate={onNavigate} />
    case 'audit':
      return <AuditTab query={query} />
  }
}

function Dashboard() {
  const [activeTab, selectTab] = useActiveTab()
  const [query, setQuery] = useState('')
  const contentRef = useRef<HTMLElement>(null)
  const contentInnerRef = useRef<HTMLDivElement>(null)

  useLenis(contentRef, contentInnerRef)

  // One search box serves two different tables, so the term does not survive a
  // tab change: carrying "failed_payment" over to the activity log would
  // silently hide most of it with no visible cause.
  useEffect(() => setQuery(''), [activeTab])

  return (
    <>
      <div className="brand-rule" />
      <div className="app">
        <RailNav activeTab={activeTab} onSelect={selectTab} />
        <SidebarNav activeTab={activeTab} onSelect={selectTab} />
        <div className="main">
          <TopBar activeTab={activeTab} query={query} onQueryChange={setQuery} />
          <main className="content" ref={contentRef}>
            <div className="content-inner" ref={contentInnerRef}>
              <TabPanel tab={activeTab}>
                <TabContent tab={activeTab} onNavigate={selectTab} query={query} />
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
  return (
    <ToastProvider>
      <Dashboard />
    </ToastProvider>
  )
}
