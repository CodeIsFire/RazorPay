/* Response shapes for the FastAPI backend, written against app/main.py and
   schema.sql. Two things worth knowing before touching anything here:

   - Money is always an integer count of paise. Never render one without
     dividing by 100 -- `amount_paise` is not rupees.
   - Timestamps come in two formats. `occurred_at` is ISO 8601 with an offset
     ("2026-08-10T09:00:00+00:00"); the SQLite datetime('now') defaults --
     `created_at`, `updated_at`, `ts` -- are space-separated UTC with no offset
     ("2026-08-23 15:48:56"). Both are typed `string` and parsed defensively,
     the same way app/analytics.py:_parse_ts handles them. */

export type Cause =
  | 'failed_payment'
  | 'fee_mismatch'
  | 'timing_lag'
  | 'duplicate'
  | 'unexplained'
  | 'refund_unmatched'
  | 'chargeback'
  | 'partial_payment'

export type ExceptionStatus = 'open' | 'pending' | 'resolved' | 'abandoned'
export type MatchedSource = 'gateway' | 'bank_statement'
export type TransactionSource = 'ledger' | 'gateway' | 'bank_statement'
export type TransactionType = 'payment' | 'refund' | 'chargeback'

export interface IntegrationStatus {
  executor: 'live' | 'mock'
  key_configured: boolean
  account_number_configured: boolean
  webhook_secret_configured: boolean
  assistant_configured: boolean
  payable_ledger_rows: number
  ledger_rows: number
}

/** GET /funnel -- returned flat, with no {count, entries} envelope. */
export interface Funnel {
  ingested: number
  matched: number
  exceptions: number
  recovered: number
  /** null when nothing has been ingested yet; rounded to 4dp otherwise. */
  match_rate: number | null
  amount_recovered_paise: number
  gateway_side_anomalies: number
}

export interface CauseBucket {
  cause: Cause
  count: number
  amount_paise: number
  share: number
}

export interface AgeBucket {
  bucket: string
  count: number
  amount_paise: number
  /** True only for the final "over Nd" bucket -- past the router's abandonment bound. */
  past_bound: boolean
  share: number
}

export interface CounterpartyBucket {
  counterparty: string
  count: number
  amount_paise: number
}

/** GET /analytics/exceptions -- also flat, no envelope. */
export interface ExceptionIntelligence {
  exception_count: number
  value_at_risk_paise: number
  max_exception_age_days: number
  by_cause: CauseBucket[]
  by_age: AgeBucket[]
  undated_count: number
  undated_paise: number
  /** Top 8 by amount (TOP_COUNTERPARTY_LIMIT in app/analytics.py). */
  top_counterparties: CounterpartyBucket[]
}

export interface DailyRow {
  /** ISO date, "YYYY-MM-DD". */
  day: string
  total_paise: number
  outstanding_paise: number
  reconciled_paise: number
  count: number
}

export interface DailyResponse {
  count: number
  days: DailyRow[]
}

export interface AuditEntry {
  id: number
  ts: string
  actor: string
  subject_type: string
  subject_id: string
  event: string
  detail: string | null
}

export interface AuditResponse {
  count: number
  entries: AuditEntry[]
}

export interface ExceptionRecord {
  id: number
  exception_key: string
  cause: Cause
  /** Null on gateway-side orphans; a comma-joined list on batch rows. */
  ledger_ref: string | null
  gateway_ref: string | null
  matched_source: MatchedSource
  amount_paise: number
  detail: string | null
  status: ExceptionStatus
  retry_count: number
  created_at: string
  updated_at: string
}

export interface ExceptionsResponse {
  count: number
  entries: ExceptionRecord[]
}

/** A row of `transactions` (schema.sql). Only source='ledger' rows fill the
    payout/fund-account/contact fields -- gateway and bank rows are
    observations, not instructions, and leave them null. */
export interface Transaction {
  id: number
  source: TransactionSource
  external_ref: string
  reference_id: string | null
  amount_paise: number
  currency: string
  counterparty: string | null
  transaction_type: TransactionType
  original_ref: string | null
  settlement_batch_id: string | null
  narration: string | null
  occurred_at: string
  payout_purpose:
    | 'refund'
    | 'cashback'
    | 'payout'
    | 'salary'
    | 'utility bill'
    | 'vendor bill'
    | null
  payout_mode: 'NEFT' | 'RTGS' | 'IMPS' | 'UPI' | 'card' | null
  fund_account_type: 'bank_account' | 'vpa' | null
  fund_account_name: string | null
  fund_account_ifsc: string | null
  fund_account_number: string | null
  fund_account_vpa: string | null
  contact_type: 'vendor' | 'customer' | 'employee' | 'self' | null
  contact_email: string | null
  contact_mobile: string | null
  contact_address: string | null
  contact_city: string | null
  contact_zipcode: string | null
  contact_state: string | null
  notes: string | null
  raw_json: string | null
  created_at: string
}

export interface ExceptionDetail {
  exception: ExceptionRecord
  ledger_transactions: Transaction[]
  actual_transactions: Transaction[]
}

export interface ReconcileResult {
  new_exceptions: string[]
  count: number
  actual_source: string
}

export interface RouteResult {
  dispatched: number
  abandoned: number
  skipped: number
  error: number
}

/** What POST /pipeline/route would do, from GET /pipeline/route/preview.
    The server runs the real routing decisions against a throwaway copy of the
    database, so these counts are the router's own answer, not an estimate. */
export interface RoutePreview {
  would_dispatch: number
  would_skip: number
  would_abandon: number
  would_error: number
  value_paise: number
}

export interface SyncPayoutsResult {
  checked: number
  confirmed: number
  still_in_flight: number
  error: number
}

/** One turn of assistant history. The wire contract is {message, history} --
    the message being asked is sent separately from the transcript. */
export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatResponse {
  reply: string
}
