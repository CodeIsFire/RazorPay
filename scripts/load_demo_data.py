"""Loads the synthetic M1 fixtures into the app's actual DB (data/recon_recover.db)
for local demo/manual testing. Deliberately NOT an HTTP endpoint: seeding fake
data is a dev-time concern, not something the real API surface should expose
once M6 wires in real RazorpayX ingestion.

Usage: python -m scripts.load_demo_data
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, session  # noqa: E402
from app.fixtures import generate_dataset  # noqa: E402
from app.load_fixtures import load_dataset_into_db  # noqa: E402


def main() -> None:
    init_db()
    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    with session() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        if existing:
            print(f"transactions table already has {existing} rows -- "
                  f"not loading demo data on top of it. Delete data/recon_recover.db "
                  f"first if you want a clean reload.")
            return
        load_dataset_into_db(conn, ledger_rows, gateway_rows)
    print(f"Loaded {len(ledger_rows)} ledger rows and {len(gateway_rows)} gateway rows.")
    print("Case summary:", ground_truth["summary"])
    print("Next: POST /pipeline/reconcile, then GET /funnel")


if __name__ == "__main__":
    main()
