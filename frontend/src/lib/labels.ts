import type { Cause, ExceptionStatus } from './types'

/* Display labels only. Every value here stays exactly as the API sends it --
   these maps are applied at render time, and nothing that filters, compares or
   exports ever sees the label. Search is the one deliberate exception: it
   matches against both, so typing "Payout Failed" finds a failed_payment row. */

export const STATUS_LABELS: Record<ExceptionStatus, string> = {
  open: 'Needs Action',
  pending: 'Processing',
  resolved: 'Processed',
  abandoned: 'Failed',
}

export const CAUSE_LABELS: Record<Cause, string> = {
  failed_payment: 'Payout Failed',
  fee_mismatch: 'Fee Mismatch',
  timing_lag: 'Pending Update',
  duplicate: 'Duplicate Transaction',
  unexplained: 'Unrecognized',
  refund_unmatched: 'Refund Not Matched',
  chargeback: 'Chargeback',
  partial_payment: 'Partial Payment',
}

/** The audit log writes machine event names. Same contract as the maps above:
    label at render time only -- the raw name still drives filtering, the pill
    colour and the CSV export. */
export const EVENT_LABELS: Record<string, string> = {
  reconciliation_complete: 'Reconcile finished',
  exception_created: 'Exception raised',
  exception_auto_resolved: 'Auto-resolved',
  exception_manually_resolved: 'Marked processed',
  partial_payment_updated: 'Partial payment updated',
  partial_payment_completed: 'Partial payment completed',
  timing_lag_recheck_resolved: 'Rechecked — now settled',
  action_dispatched: 'Action dispatched',
  action_dispatch_failed: 'Dispatch failed',
  action_skipped_bound_hit: 'Retry limit reached',
  action_skipped_in_flight: 'Held — earlier attempt still running',
  payout_confirmed_processed: 'Payout confirmed',
  payout_confirmed_reversed: 'Payout reversed',
  payout_synced_from_api: 'Status synced from RazorpayX',
  payout_sync_failed: 'Status sync failed',
  webhook_received: 'Webhook received',
  webhook_unmatched: 'Webhook — no matching payout',
}

export const statusLabel = (s: string) => STATUS_LABELS[s as ExceptionStatus] ?? s
export const causeLabel = (c: string) => CAUSE_LABELS[c as Cause] ?? c
export const eventLabel = (e: string) => EVENT_LABELS[e] ?? e.replace(/_/g, ' ')

/** Matches raw audit event names as the backend writes them -- these are not
    display labels and must stay in the API's vocabulary. 'completed' (not
    'complete') is its own outcome word so partial_payment_completed reads as a
    resolution (green), the same family as exception_auto_resolved and
    payout_confirmed_processed -- distinct from reconciliation_complete, which
    is a neutral "a run finished" event and stays in the blue group. */
export function eventPillClass(event: string): string {
  if (/resolved|recovered|processed|completed/.test(event)) return 'pill-green'
  if (/failed|error|abandoned|reversed|unmatched/.test(event)) return 'pill-red'
  if (/dispatched|created|received|complete|updated/.test(event)) return 'pill-blue'
  if (/skipped|pending/.test(event)) return 'pill-orange'
  return ''
}

export function statusPillClass(status: string): string {
  if (status === 'resolved') return 'pill-green'
  if (status === 'pending') return 'pill-blue'
  if (status === 'abandoned') return 'pill-red'
  return 'pill-orange'
}

export type TabId = 'overview' | 'exceptions' | 'insights' | 'audit'

export const TAB_IDS: TabId[] = ['overview', 'exceptions', 'insights', 'audit']

export const TAB_TITLES: Record<TabId, string> = {
  overview: 'Overview',
  exceptions: 'Needs attention',
  insights: 'Insights',
  audit: 'Activity log',
}

/** Only the two tabs with a table get the search box; the others hide it
    rather than leaving an inert control on screen. */
export const SEARCH_PLACEHOLDERS: Partial<Record<TabId, string>> = {
  exceptions: 'Search cause, reference, detail…',
  audit: 'Search event, actor, detail…',
}

/** Whether a string is a cause this frontend knows how to filter by.

    Guards the drill-through from charts: an unchecked cast would let a cause
    the backend added but this build has not land in causeFilter, matching no
    row, and the table would render "nothing needs attention" -- which is the
    exact "these records vanished" failure the age and counterparty charts were
    deliberately left undrillable to avoid. */
export function isKnownCause(value: string): value is Cause {
  return value in CAUSE_LABELS
}
