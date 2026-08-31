import type { AuditEntry } from './types'

/** The activity log's only filter is the search box, so this is also what its
    CSV export writes -- narrowing the search is how you choose what to export.
    (The exceptions export deliberately does the opposite.) */
export function filterAudit(all: AuditEntry[], query: string): AuditEntry[] {
  const q = query.trim().toLowerCase()
  if (!q) return all
  return all.filter((e) =>
    [e.event, e.actor, e.subject_type, e.subject_id, e.detail]
      .join(' ')
      .toLowerCase()
      .includes(q),
  )
}
