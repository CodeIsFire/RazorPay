import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { exportAuditCsv, exportExceptionsCsv } from './csv'
import type { AuditEntry, ExceptionRecord } from './types'

/* The two exports differ on purpose -- exceptions ignores the filters on
   screen, audit respects them -- and the easy mistake is to make them
   consistent with each other. Both directions are asserted here. */

let written = ''
let filename = ''

beforeEach(() => {
  written = ''
  filename = ''
  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
    // Blob.text() is async; the CSV is small enough to read synchronously
    // from the parts we were handed instead.
    written = (blob as Blob & { __parts?: string[] }).__parts?.join('') ?? ''
    return 'blob:stub'
  })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    filename = this.download
  })
  // jsdom's Blob does not expose its parts, so capture them at construction.
  const RealBlob = globalThis.Blob
  vi.stubGlobal(
    'Blob',
    class extends RealBlob {
      __parts: string[]
      constructor(parts: string[], opts?: BlobPropertyBag) {
        super(parts, opts)
        this.__parts = parts
      }
    },
  )
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const exception = (over: Partial<ExceptionRecord> = {}): ExceptionRecord => ({
  id: 1,
  exception_key: 'k1',
  cause: 'failed_payment',
  ledger_ref: 'LED-0056',
  gateway_ref: null,
  matched_source: 'gateway',
  amount_paise: 3252500,
  detail: 'No bank statement transaction found.',
  status: 'resolved',
  retry_count: 2,
  created_at: '2026-08-30 15:00:00',
  updated_at: '2026-08-30 15:51:54',
  ...over,
})

const audit = (over: Partial<AuditEntry> = {}): AuditEntry => ({
  id: 1,
  ts: '2026-08-30 15:51:54',
  actor: 'router',
  subject_type: 'exception',
  subject_id: 'k1',
  event: 'payout_confirmed_processed',
  detail: 'action_id=56',
  ...over,
})

describe('exportExceptionsCsv', () => {
  it('writes the API vocabulary, not the on-screen labels', () => {
    // The file has to stay joinable with GET /exceptions, so "failed_payment"
    // and "resolved" must survive rather than becoming "Payout Failed" and
    // "Processed".
    exportExceptionsCsv([exception()])
    expect(written).toContain('"failed_payment"')
    expect(written).toContain('"resolved"')
    expect(written).not.toContain('Payout Failed')
    expect(written).not.toContain('"Processed"')
  })

  it('states the amount in rupees', () => {
    exportExceptionsCsv([exception({ amount_paise: 3252500 })])
    expect(written).toContain('"32525.00"')
  })

  it('escapes a quote inside a detail string', () => {
    exportExceptionsCsv([exception({ detail: 'said "short by 300"' })])
    expect(written).toContain('"said ""short by 300"""')
  })

  it('names the file the way the dashboard always has', () => {
    exportExceptionsCsv([exception()])
    expect(filename).toBe('needs-attention.csv')
  })

  it('writes every row it is given', () => {
    exportExceptionsCsv([exception({ id: 1 }), exception({ id: 2 }), exception({ id: 3 })])
    expect(written.split('\r\n')).toHaveLength(4) // header + 3
  })
})

describe('exportAuditCsv', () => {
  it('writes only the rows it is given, so the search filter carries through', () => {
    // The activity log's ONLY filter is the search box -- narrowing it is how
    // you choose what to export, unlike the exceptions table.
    exportAuditCsv([audit({ id: 1 })])
    expect(written.split('\r\n')).toHaveLength(2) // header + 1
  })

  it('joins the subject into one column', () => {
    exportAuditCsv([audit({ subject_type: 'exception', subject_id: 'k1' })])
    expect(written).toContain('"exception:k1"')
  })

  it('names the file the way the dashboard always has', () => {
    exportAuditCsv([audit()])
    expect(filename).toBe('activity-log.csv')
  })
})
