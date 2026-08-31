import { useState } from 'react'
import { ExceptionFilters } from '@/components/exceptions/ExceptionFilters'
import { ExceptionsTable } from '@/components/exceptions/ExceptionsTable'
import { IconDownload, IconRefresh } from '@/components/icons'
import { LoadFailed } from '@/components/LoadFailed'
import { useToast } from '@/components/Toast'
import { useDelayedFlag } from '@/hooks/useDelayedFlag'
import { ApiError } from '@/lib/api'
import { exportExceptionsCsv } from '@/lib/csv'
import {
  filterExceptions,
  type QuickFilter,
  type SortKey,
  type SortOrder,
} from '@/lib/exceptionFilters'
import type { Cause } from '@/lib/types'
import { useExceptions, useRecheckException, useResolveException } from '@/lib/queries'

export function ExceptionsTab({
  query,
  causeFocus,
}: {
  query: string
  causeFocus?: Cause | null
}) {
  /* Arriving from a chart scopes the table to the backlog, because that is the
     population the chart counted. Arriving any other way shows everything. */
  const [quickFilter, setQuickFilter] = useState<QuickFilter>(causeFocus ? 'backlog' : 'all')
  const [statusFilter, setStatusFilter] = useState('')
  /* Seeded from the arriving navigation rather than synced by an effect. The
     panel is keyed on the tab id, so this component genuinely remounts on every
     visit -- initial state is read each time you arrive, and there is no window
     where the table has rendered unfiltered before a filter is applied. */
  const [causeFilter, setCauseFilter] = useState<string>(causeFocus ?? '')
  const [sort, setSort] = useState<SortOrder>('desc')
  const [sortKey, setSortKey] = useState<SortKey>('updated_at')

  /* Clicking the active column flips direction; clicking a new one adopts it.
     New columns start descending because every one of them is a "most first"
     question -- biggest amount, most retries, latest update. Ascending is the
     second press, not the default. */
  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSort((cur) => (cur === 'asc' ? 'desc' : 'asc'))
      return
    }
    setSortKey(key)
    setSort('desc')
  }
  const [expandedKey, setExpandedKey] = useState<string | null>(null)

  const exceptions = useExceptions()
  const resolve = useResolveException()
  const recheck = useRecheckException()
  const toast = useToast()

  const all = exceptions.data?.entries ?? []
  const rows = filterExceptions(all, { quickFilter, statusFilter, causeFilter, query, sort, sortKey })
  // isPending, not isFetching: the poll must never replace rows an operator is
  // reading with a skeleton. See the note in OverviewTab.
  const listLoading = useDelayedFlag(exceptions.isPending)

  const busyKey =
    (resolve.isPending && resolve.variables) || (recheck.isPending && recheck.variables) || null

  async function act(
    key: string,
    run: (key: string) => Promise<unknown>,
    done: string,
  ) {
    try {
      await run(key)
      toast(done)
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'That action failed.', true)
    }
  }

  return (
    <>
      <p className="page-desc">
        Ledger records that didn't reconcile automatically, grouped by cause and worked by the
        recovery agent.
      </p>

      <ExceptionFilters
        quickFilter={quickFilter}
        statusFilter={statusFilter}
        causeFilter={causeFilter}
        // Picking a chip clears the select and vice versa: they answer the
        // same question, and leaving both set would silently intersect them.
        onQuickFilter={(q) => {
          setQuickFilter(q)
          setStatusFilter('')
        }}
        onStatusFilter={(s) => {
          setStatusFilter(s)
          setQuickFilter('all')
        }}
        onCauseFilter={setCauseFilter}
      />

      <div className="notice">
        <span className="pill pill-orange">Test</span>
        Detected against RazorpayX test-mode transactions. Routing calls the real test-mode
        Payouts API and moves no real money.
      </div>

      <section className="card">
        <div className="table-toolbar">
          <div className="left">
            <span className="count">
              <b>{rows.length}</b> records
            </span>
            {/* The sort select is gone: ordering now lives on the column
                headers, where the thing being ordered actually is. Keeping
                both would be two controls for one piece of state. */}
          </div>
          <div className="right">
            <button
              className="btn icon-btn"
              title="Refresh"
              aria-label="Refresh"
              onClick={async () => {
                const r = await exceptions.refetch()
                toast(
                  r.isError
                    ? 'Couldn’t refresh the exception list.'
                    : 'Needs attention refreshed.',
                  r.isError,
                )
              }}
            >
              <IconRefresh />
            </button>
            {/* Exports every row the API returned, not the filtered view, and
                in the API's own vocabulary -- so the file stays joinable with
                GET /exceptions. */}
            <button
              className="btn icon-btn"
              title="Export CSV"
              aria-label="Export CSV"
              onClick={() => exportExceptionsCsv(all)}
            >
              <IconDownload />
            </button>
          </div>
        </div>

        {/* Error before the table: "Nothing needs attention under this filter"
            is the most dangerous sentence this app can show when the truth is
            that the request failed. */}
        {exceptions.isError ? (
          <LoadFailed
            what="the exception list"
            error={exceptions.error}
            onRetry={() => exceptions.refetch()}
            retrying={exceptions.isFetching}
          />
        ) : (
        <ExceptionsTable
          rows={rows}
          loading={listLoading}
          sortKey={sortKey}
          sort={sort}
          onSort={toggleSort}
          expandedKey={expandedKey}
          busyKey={busyKey || null}
          emptyMessage={
            query.trim()
              ? 'No records match this search.'
              : 'Nothing needs attention under this filter.'
          }
          onToggleExpand={(key) => setExpandedKey((cur) => (cur === key ? null : key))}
          onResolve={(key) => act(key, resolve.mutateAsync, 'Marked processed.')}
          onRecheck={(key) => act(key, recheck.mutateAsync, 'Rechecked against the gateway.')}
        />
        )}
      </section>
    </>
  )
}
