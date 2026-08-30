-- Reconcile -> Recover schema.
-- Kept intentionally small at M0: only the tables every later stage needs
-- on day one. M3/M4/M5 extend this file rather than inventing a parallel one.

PRAGMA foreign_keys = ON;

-- Every row ingested from any source, unified. `source` distinguishes the
-- synthetic ledger (the "expected" side, always 'ledger') from every
-- "actual" side reconcile.py can be pointed at -- RazorpayX test-mode
-- transactions ('gateway') or a real bank statement ('bank_statement') --
-- so the matcher in M2 can query any of them through one table. Adding a
-- fourth "actual" source (a settlement file, an ERP export, ...) means
-- adding one more literal here, not a new table: reconcile()/classify()
-- neither know nor care what the non-ledger source is called, only
-- reconcile_from_db()'s SQL and this constraint do.
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL CHECK (source IN ('ledger', 'gateway', 'bank_statement')),
    external_ref    TEXT NOT NULL,          -- ledger order id, or RazorpayX payout/transaction id
    reference_id    TEXT,                   -- shared correlation key: on ledger rows, their own id;
                                             -- on gateway rows, the ledger id we asked RazorpayX to
                                             -- echo back (payouts support a reference_id field).
                                             -- Absent when a payout was created without one, forcing
                                             -- the matcher's fuzzy fallback.
    amount_paise    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'INR',
    counterparty    TEXT,                   -- contact/fund account name or id, if known
    transaction_type TEXT NOT NULL DEFAULT 'payment'
                     CHECK (transaction_type IN ('payment', 'refund', 'chargeback')),
    original_ref    TEXT,                   -- for refund/chargeback rows: the external_ref of the
                                             -- payment being reversed, so a reversal is always
                                             -- traceable to its origin rather than reconciled as
                                             -- an independent transaction (see app/classify.py).
    settlement_batch_id TEXT,               -- ledger rows sharing this id are settled together by
                                             -- one gateway row whose reference_id equals the batch
                                             -- id (not any single ledger row's own id) -- the N:1
                                             -- batch-settlement direction of partial-payment
                                             -- matching (see app/reconcile.py).
    narration       TEXT,
    occurred_at     TEXT NOT NULL,           -- ISO 8601

    -- ---- RazorpayX payout instruction (Tally batch-payout template) ----
    -- Shaped after RazorpayX's Tally batch-payout CSV, so a ledger row IS a
    -- payout instruction: everything create_payout() needs lives on the row
    -- instead of in config defaults plus a side-car fund_account_map.json.
    --
    -- All nullable, and deliberately so: only source='ledger' rows are payout
    -- instructions. 'gateway'/'bank_statement' rows are observations of a
    -- payout that already happened and leave every one of these NULL. SQLite
    -- CHECK passes when its expression is NULL, so the constrained columns
    -- need no extra IS NULL guard.
    --
    -- Four template columns are NOT repeated here because the existing schema
    -- already carries them: payout reference id -> reference_id, payout amount
    -- -> amount_paise (the template is in rupees; x100 on the way in), payout
    -- date -> occurred_at, contact name -> counterparty. The template's first
    -- column, the RazorpayX business account number, is per-account not
    -- per-row and lives in config.RAZORPAYX_ACCOUNT_NUMBER.
    payout_purpose      TEXT CHECK (payout_purpose IN
                            ('refund', 'cashback', 'payout', 'salary',
                             'utility bill', 'vendor bill')),
    payout_mode         TEXT CHECK (payout_mode IN ('NEFT', 'RTGS', 'IMPS', 'UPI', 'card')),
    -- 'bank_account' rows carry ifsc + number and no vpa; 'vpa' rows carry
    -- vpa and neither of the other two. Never both -- asserted in tests.
    fund_account_type   TEXT CHECK (fund_account_type IN ('bank_account', 'vpa')),
    fund_account_name   TEXT,
    fund_account_ifsc   TEXT,
    fund_account_number TEXT,
    fund_account_vpa    TEXT,
    contact_type        TEXT CHECK (contact_type IN ('vendor', 'customer', 'employee', 'self')),
    contact_email       TEXT,
    contact_mobile      TEXT,
    contact_address     TEXT,
    contact_city        TEXT,
    contact_zipcode     TEXT,
    contact_state       TEXT,
    notes               TEXT,

    raw_json        TEXT,                    -- original row, verbatim, for debugging/audit
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_source_ref
    ON transactions (source, external_ref);

CREATE INDEX IF NOT EXISTS idx_transactions_reference_id
    ON transactions (reference_id);

CREATE INDEX IF NOT EXISTS idx_transactions_amount
    ON transactions (amount_paise);

-- Cross-cutting decision log. Every stage (matcher, classifier, router,
-- webhook handler) writes here — including the branches where nothing
-- happened, e.g. a retry/age bound was hit. This table is what the
-- dashboard's audit view and the buildathon's "graceful failure handling"
-- judging criterion both read from.
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL DEFAULT (datetime('now')),
    actor        TEXT NOT NULL,     -- 'reconciler' | 'classifier' | 'router' | 'webhook' | ...
    subject_type TEXT NOT NULL,     -- 'transaction' | 'exception' | 'action' | ...
    subject_id   TEXT NOT NULL,
    event        TEXT NOT NULL,     -- short machine-readable event name
    detail       TEXT               -- free-text or JSON explanation
);

CREATE INDEX IF NOT EXISTS idx_audit_log_subject
    ON audit_log (subject_type, subject_id);

-- One row per unmatched transaction (or unmatched pair), typed by likely
-- cause. `exception_key` is the load-bearing field: a deterministic id
-- derived from (cause, ledger_ref, gateway_ref) -- see app/classify.py --
-- not a DB autoincrement. Re-running the classifier never creates a
-- duplicate row for the same underlying situation, and M5's retry/
-- idempotency-key convention for RazorpayX payouts is built on this same
-- key rather than a second identifier scheme.
CREATE TABLE IF NOT EXISTS exceptions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    exception_key  TEXT NOT NULL UNIQUE,
    cause          TEXT NOT NULL CHECK (
                       cause IN ('failed_payment', 'fee_mismatch', 'duplicate',
                                 'timing_lag', 'unexplained', 'refund_unmatched',
                                 'chargeback', 'partial_payment')
                   ),
    ledger_ref     TEXT,     -- transactions.external_ref where source='ledger', if any
    gateway_ref    TEXT,     -- transactions.external_ref of the correlated non-ledger row, if
                             -- any -- named for the original gateway-only pipeline; see
                             -- matched_source for which actual source it actually came from
    matched_source TEXT NOT NULL DEFAULT 'gateway'
                   CHECK (matched_source IN ('gateway', 'bank_statement')),
    amount_paise   INTEGER NOT NULL,
    detail         TEXT,
    -- No 'in_progress': nothing in the app branched on it differently from
    -- 'open' (retry_count already tells you whether an attempt's been
    -- made), so it was a distinction without a use -- see app/router.py.
    status         TEXT NOT NULL DEFAULT 'open' CHECK (
                       status IN ('open', 'pending', 'resolved', 'abandoned')
                   ),
    retry_count    INTEGER NOT NULL DEFAULT 0,  -- owned by M5's action router, not written here
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_exceptions_cause ON exceptions (cause);
CREATE INDEX IF NOT EXISTS idx_exceptions_status ON exceptions (status);

-- One row per attempt, not per exception -- a retried payout gets a new
-- row each attempt, all sharing exception_key so the full retry history
-- is visible. `idempotency_key` is what becomes the RazorpayX payout's
-- reference_id at M6: derived from exception_key + attempt_number, so a
-- retried attempt can never collide with a previous one, and any attempt
-- can be traced back to the exception that caused it.
CREATE TABLE IF NOT EXISTS actions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    exception_key     TEXT NOT NULL REFERENCES exceptions(exception_key),
    attempt_number    INTEGER NOT NULL,
    action_type       TEXT NOT NULL CHECK (
                          action_type IN ('retry_payout', 'draft_dispute_note',
                                          'send_reminder', 'flag_for_review')
                      ),
    idempotency_key   TEXT NOT NULL UNIQUE,
    -- 'queued'/'processing'/'processed'/'reversed'/'failed'/'rejected' are
    -- RazorpayX's own payout status vocabulary, passed through verbatim
    -- (see app/live_executor.py) rather than collapsed into our own names
    -- -- only the confirm step (mocked now, a real webhook at M6) may set
    -- a terminal outcome; dispatch alone never produces 'processed'.
    -- 'completed' is this app's own label for the three non-payout action
    -- types (draft_dispute_note/send_reminder/flag_for_review), which
    -- finish the instant they're dispatched. No DEFAULT: every insert sets
    -- this explicitly (see app/router.py), so a future insert that forgets
    -- to should fail loudly rather than silently land on a placeholder.
    status            TEXT NOT NULL CHECK (
                          status IN ('queued', 'processing', 'processed',
                                     'reversed', 'failed', 'rejected', 'completed')
                      ),
    gateway_payout_id TEXT,
    detail            TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_actions_exception_key ON actions (exception_key);
