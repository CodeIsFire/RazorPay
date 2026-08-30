import { IconDownload, IconRefresh } from '@/components/icons'
import { useToast } from '@/components/Toast'
import { filterAudit } from '@/lib/auditFilters'
import { exportAuditCsv } from '@/lib/csv'
import { fmtTs, formatDetail } from '@/lib/format'
import { eventLabel, eventPillClass } from '@/lib/labels'
import { useAudit } from '@/lib/queries'

export function AuditTab({ query }: { query: string }) {
  const audit = useAudit()
  const toast = useToast()

  const all = audit.data?.entries ?? []
  const entries = filterAudit(all, query)

  return (
    <>
      <p className="page-desc">
        Every pipeline stage writes here — the single source of truth for why something did or
        didn't happen.
      </p>

      <section className="card">
        <div className="table-toolbar">
          <div className="left">
            <span className="count">
              <b>{entries.length}</b> entries
            </span>
          </div>
          <div className="right">
            <button
              className="btn icon-btn"
              title="Refresh"
              aria-label="Refresh"
              onClick={async () => {
                await audit.refetch()
                toast('Activity log refreshed.')
              }}
            >
              <IconRefresh />
            </button>
            {/* Exports what is on screen, search included -- the opposite of
                the exceptions export, and deliberately so: the search box is
                this table's only filter. */}
            <button
              className="btn icon-btn"
              title="Export CSV"
              aria-label="Export CSV"
              onClick={() => exportAuditCsv(entries)}
            >
              <IconDownload />
            </button>
          </div>
        </div>

        <div className="table-scroll" data-lenis-prevent>
          {entries.length ? (
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Actor</th>
                  <th>Event</th>
                  <th>Subject</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => {
                  const detail = formatDetail(e.detail)
                  const subject = `${e.subject_type}:${e.subject_id}`
                  return (
                    <tr key={e.id}>
                      <td className="ts">{fmtTs(e.ts, { seconds: true })}</td>
                      <td className="ref">{e.actor}</td>
                      <td>
                        {/* The pill shows the humanised label; its tooltip
                            keeps the raw event name, which is what you would
                            grep the audit log for. */}
                        <span className={`pill ${eventPillClass(e.event)}`} title={e.event}>
                          {eventLabel(e.event)}
                        </span>
                      </td>
                      <td className="ref" title={subject}>
                        {subject}
                      </td>
                      <td className="detail" title={detail}>
                        <span className="clamp">{detail}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <div className="empty">
              {query.trim()
                ? 'No entries match this search.'
                : 'No pipeline activity yet — open Run and start with Reconcile.'}
            </div>
          )}
        </div>
      </section>
    </>
  )
}
