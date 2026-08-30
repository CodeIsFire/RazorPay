import type { AuditEntry, ExceptionRecord } from './types'

function downloadCSV(filename: string, headers: string[], rows: (string | number)[][]) {
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const csv = [headers.map(esc).join(','), ...rows.map((r) => r.map(esc).join(','))].join('\r\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/* The two exports differ on purpose, and the difference is not an oversight:

   Exceptions exports every row the API returned, ignoring the filters on
   screen, and writes RAW api values rather than display labels -- so the file
   stays joinable with GET /exceptions.

   Audit exports what you are currently looking at, filters included, because
   the activity log's only filter IS the search box: narrowing it is how you
   pick the rows you meant to export. */

export function exportExceptionsCsv(all: ExceptionRecord[]) {
  downloadCSV(
    'exceptions.csv',
    ['Cause', 'Ledger Ref', 'UTR/Payout ID', 'Amount (INR)', 'Status', 'Retries', 'Detail', 'Updated'],
    all.map((e) => [
      e.cause,
      e.ledger_ref ?? '',
      e.gateway_ref ?? '',
      (e.amount_paise / 100).toFixed(2),
      e.status,
      e.retry_count,
      e.detail ?? '',
      e.updated_at,
    ]),
  )
}

export function exportAuditCsv(visible: AuditEntry[]) {
  downloadCSV(
    'activity-log.csv',
    ['Time', 'Actor', 'Event', 'Subject', 'Detail'],
    visible.map((a) => [
      a.ts,
      a.actor,
      a.event,
      `${a.subject_type}:${a.subject_id}`,
      a.detail ?? '',
    ]),
  )
}
