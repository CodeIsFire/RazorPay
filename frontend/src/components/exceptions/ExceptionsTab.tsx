import { useState } from 'react'
import { ExceptionFilters } from '@/components/exceptions/ExceptionFilters'
import { ExceptionsTable } from '@/components/exceptions/ExceptionsTable'
import { IconDownload, IconRefresh } from '@/components/icons'
import { useToast } from '@/components/Toast'
import { ApiError } from '@/lib/api'
import { exportExceptionsCsv } from '@/lib/csv'
import { filterExceptions, type QuickFilter, type SortOrder } from '@/lib/exceptionFilters'
import { useExceptions, useRecheckException, useResolveException } from '@/lib/queries'

export function ExceptionsTab({ query }: { query: string }) {
  const [quickFilter, setQuickFilter] = useState<QuickFilter>('all')
  const [statusFilter, setStatusFilter] = useState('')
  const [causeFilter, setCauseFilter] = useState('')
  const [sort, setSort] = useState<SortOrder>('desc')
  const [expandedKey, setExpandedKey] = useState<string | null>(null)

  const exceptions = useExceptions()
  const resolve = useResolveException()
  const recheck = useRecheckException()
  const toast = useToast()

  const all = exceptions.data?.entries ?? []
  const rows = filterExceptions(all, { quickFilter, statusFilter, causeFilter, query, sort })

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
            <select
              className="filter-select"
              aria-label="Sort order"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortOrder)}
            >
              <option value="desc">Sort: latest update first</option>
              <option value="asc">Sort: oldest update first</option>
            </select>
          </div>
          <div className="right">
            <button
              className="btn icon-btn"
              title="Refresh"
              aria-label="Refresh"
              onClick={async () => {
                await exceptions.refetch()
                toast('Needs attention refreshed.')
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

        <ExceptionsTable
          rows={rows}
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
      </section>
    </>
  )
}
