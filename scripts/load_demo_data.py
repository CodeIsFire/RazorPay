"""Loads the synthetic fixtures into the app's actual DB (data/recon_recover.db)
for local demo/manual testing. Deliberately NOT an HTTP endpoint: seeding fake
data is a dev-time concern, not something the real API surface should expose
once M6 wires in real RazorpayX ingestion.

Loads all three generators, so every one of classify.py's eight causes is
represented:

  generate_dataset()             ledger + gateway  -> exact_match, fee_mismatch,
                                                      duplicate, timing_lag,
                                                      failed_payment, unexplained
  generate_split_and_batch_cases()  ledger + gateway  -> partial_payment
  generate_bank_statement()         bank_statement    -> refund_unmatched,
                                                         chargeback
                                                      (reconciled separately)

generate_bank_statement() only covers generate_dataset()'s ledger rows -- its
BANK_CASE_PLAN is deliberately strict about that -- so the split/batch ledger
rows would have no bank-side view at all. Reconciling against 'bank_statement'
would then report six failed_payments that are purely an artefact of the
fixture, not a finding. generate_settled_bank_lines() closes that gap with
plain settled debits.

Usage: python -m scripts.load_demo_data
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, session  # noqa: E402
from app.fixtures import (  # noqa: E402
    generate_bank_statement,
    generate_dataset,
    generate_settled_bank_lines,
    generate_split_and_batch_cases,
)
from app.load_fixtures import load_transactions  # noqa: E402


def main() -> None:
    init_db()

    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    split_ledger, split_gateway, split_truth = generate_split_and_batch_cases()
    bank_rows, bank_truth = generate_bank_statement(ledger_rows)
    bank_rows += generate_settled_bank_lines(split_ledger)

    with session() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        if existing:
            print(f"transactions table already has {existing} rows -- "
                  f"not loading demo data on top of it. Delete data/recon_recover.db "
                  f"(and its -wal/-shm sidecars) first if you want a clean reload.")
            return
        for rows in (ledger_rows, gateway_rows, split_ledger, split_gateway, bank_rows):
            load_transactions(conn, rows)
        conn.commit()

    print(f"ledger        : {len(ledger_rows) + len(split_ledger)} rows "
          f"({len(ledger_rows)} main + {len(split_ledger)} split/batch)")
    print(f"gateway       : {len(gateway_rows) + len(split_gateway)} rows")
    print(f"bank_statement: {len(bank_rows)} rows")
    print()
    # generate_split_and_batch_cases() returns no "summary" -- its ground truth
    # is a plain case list -- so count the case types here rather than assume
    # all three generators share a shape.
    split_summary = Counter(case["case_type"] for case in split_truth["cases"])
    print("gateway cases     :", ground_truth["summary"])
    print("split/batch cases :", dict(split_summary))
    print("bank cases        :", bank_truth["summary"])
    print()
    print("Next: POST /pipeline/reconcile           (gateway)")
    print("      POST /pipeline/reconcile?actual_source=bank_statement")


if __name__ == "__main__":
    main()
