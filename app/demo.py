"""Restoring the app to its out-of-the-box demo state.

This deliberately reverses a decision recorded in scripts/load_demo_data.py:
that seeding fixtures is "a dev-time concern, not something the real API
surface should expose". That held while the only way to change the data was
to run a script -- someone already at a shell can re-run the script.

The upload feature broke that assumption. A user can now replace the ledger
and bank statement from the browser, the landing page invites them to ("Try
it yourself"), and once they have, the demo data is gone with no way back
that does not involve deleting a file on disk. A reset that lives only in a
terminal is no reset at all for the person who needs it.

What is NOT exposed, and stays a dev-time concern: choosing what to seed.
There are no parameters here -- this loads the same fixed, deterministic
dataset the script does, or nothing.

It is also the one place that reconciles both actual sources. Seeding
without reconciling lands on a dashboard reading 0% matched with an empty
backlog, which looks broken rather than fresh -- so the reset finishes the
job. It never routes: reconciling only detects, while routing dispatches
payouts, and no button labelled "reload data" should move money.
"""
from __future__ import annotations

import sqlite3

from app.classify import classify_and_persist_from_db
from app.db import log_audit
from app.fixtures import (
    generate_bank_statement,
    generate_dataset,
    generate_settled_bank_lines,
    generate_split_and_batch_cases,
)
from app.load_fixtures import load_transactions
from app.reconcile import ACTUAL_SOURCES


def reset_demo_data(conn: sqlite3.Connection) -> dict:
    """Clear everything, reseed the fixtures, reconcile both sources.

    One transaction: a half-applied reset would leave the app emptier than
    it found it, which is strictly worse than not having pressed the button.
    """
    ledger_rows, gateway_rows, _ = generate_dataset()
    split_ledger, split_gateway, _ = generate_split_and_batch_cases()
    bank_rows, _ = generate_bank_statement(ledger_rows)
    bank_rows += generate_settled_bank_lines(split_ledger)

    try:
        # Order matters: actions reference exceptions by foreign key.
        conn.execute("DELETE FROM actions")
        conn.execute("DELETE FROM exceptions")
        conn.execute("DELETE FROM transactions")
        for rows in (ledger_rows, gateway_rows, split_ledger, split_gateway, bank_rows):
            load_transactions(conn, rows)

        counts = {
            "ledger": len(ledger_rows) + len(split_ledger),
            "gateway": len(gateway_rows) + len(split_gateway),
            "bank_statement": len(bank_rows),
        }
        log_audit(conn, actor="demo", subject_type="transaction",
                  subject_id="all", event="demo_data_reset",
                  detail=(f"ledger={counts['ledger']} gateway={counts['gateway']} "
                          f"bank_statement={counts['bank_statement']}"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # After the commit, not inside it: classify_and_persist_from_db commits
    # its own work and writes its own audit trail, exactly as it does for a
    # manual POST /pipeline/reconcile. Nothing here should reimplement it.
    exceptions = 0
    for source in ACTUAL_SOURCES:
        exceptions += len(classify_and_persist_from_db(conn, source))

    return {**counts, "exceptions": exceptions}
