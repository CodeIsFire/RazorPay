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
