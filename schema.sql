-- Reconcile -> Recover schema.
-- Kept intentionally small at M0: only the tables every later stage needs
-- on day one. M3/M4/M5 extend this file rather than inventing a parallel one.

PRAGMA foreign_keys = ON;

-- Every row ingested from either source, unified. `source` distinguishes
-- the synthetic ledger (the "expected" side) from RazorpayX test-mode
-- transactions (the "actual" side) so the matcher in M2 can query both
-- through one table.
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL CHECK (source IN ('ledger', 'gateway')),
    external_ref    TEXT NOT NULL,          -- ledger order id, or RazorpayX payout/transaction id
    reference_id    TEXT,                   -- shared correlation key: on ledger rows, their own id;
                                             -- on gateway rows, the ledger id we asked RazorpayX to
                                             -- echo back (payouts support a reference_id field).
                                             -- Absent when a payout was created without one, forcing
                                             -- the matcher's fuzzy fallback.
    amount_paise    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'INR',
    counterparty    TEXT,                   -- contact/fund account name or id, if known
    narration       TEXT,
    occurred_at     TEXT NOT NULL,           -- ISO 8601
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
                                 'timing_lag', 'unexplained')
                   ),
    ledger_ref     TEXT,     -- transactions.external_ref where source='ledger', if any
    gateway_ref    TEXT,     -- transactions.external_ref where source='gateway', if any
    amount_paise   INTEGER NOT NULL,
    detail         TEXT,
    status         TEXT NOT NULL DEFAULT 'open' CHECK (
                       status IN ('open', 'in_progress', 'resolved', 'abandoned')
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
    -- 'processing'/'processed'/'reversed' only ever apply to retry_payout,
    -- and only the confirm step (mocked now, a real webhook at M6) may set
    -- processed/reversed -- dispatch alone only ever produces 'processing'.
    status            TEXT NOT NULL DEFAULT 'dispatched' CHECK (
                          status IN ('dispatched', 'processing', 'processed',
                                     'reversed', 'completed')
                      ),
    gateway_payout_id TEXT,
    detail            TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_actions_exception_key ON actions (exception_key);
