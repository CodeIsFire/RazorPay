import { describe, expect, it } from 'vitest'
import { filterAudit } from './auditFilters'
import { eventPillClass } from './labels'
import type { AuditEntry } from './types'

let seq = 0
const entry = (over: Partial<AuditEntry> = {}): AuditEntry => ({
  id: ++seq,
  ts: '2026-08-30 15:51:54',
  actor: 'router',
  subject_type: 'exception',
  subject_id: 'failed_payment:gateway:LED-0056:-',
  event: 'action_dispatched',
  detail: 'action_id=56',
  ...over,
})

describe('filterAudit', () => {
  const rows = [
    entry({ event: 'payout_confirmed_processed', actor: 'webhook' }),
    entry({ event: 'action_dispatched', actor: 'router' }),
    entry({ event: 'reconciliation_complete', actor: 'reconciler', detail: 'matched=36' }),
  ]

  it('returns everything for an empty search', () => {
    expect(filterAudit(rows, '')).toHaveLength(3)
    expect(filterAudit(rows, '   ')).toHaveLength(3)
  })

  it('returns the same array instance when unfiltered', () => {
    // 200 rows re-rendering on every keystroke is worth avoiding.
    expect(filterAudit(rows, '')).toBe(rows)
  })

  it('matches the event name', () => {
    expect(filterAudit(rows, 'payout_confirmed')).toHaveLength(1)
  })

  it('matches the actor', () => {
    expect(filterAudit(rows, 'reconciler')).toHaveLength(1)
  })

  it('matches inside the detail string', () => {
    expect(filterAudit(rows, 'matched=36')).toHaveLength(1)
  })

  it('matches the subject id', () => {
    expect(filterAudit(rows, 'LED-0056')).toHaveLength(3)
  })
})

describe('eventPillClass', () => {
  it('reads an outcome as green', () => {
    expect(eventPillClass('exception_auto_resolved')).toBe('pill-green')
    expect(eventPillClass('payout_confirmed_processed')).toBe('pill-green')
    expect(eventPillClass('partial_payment_completed')).toBe('pill-green')
  })

  it('separates a finished run from a resolved record', () => {
    // "completed" is an outcome word; "complete" is just "a run finished".
    // reconciliation_complete must stay neutral rather than reading as green.
    expect(eventPillClass('reconciliation_complete')).toBe('pill-blue')
  })

  it('reads a failure as red', () => {
    expect(eventPillClass('action_dispatch_failed')).toBe('pill-red')
    expect(eventPillClass('payout_confirmed_reversed')).toBe('pill-red')
    expect(eventPillClass('webhook_unmatched')).toBe('pill-red')
  })

  it('reads a hold as orange', () => {
    expect(eventPillClass('action_skipped_in_flight')).toBe('pill-orange')
  })

  it('leaves an unrecognised event unpainted rather than guessing', () => {
    expect(eventPillClass('something_new_entirely')).toBe('')
  })
})
