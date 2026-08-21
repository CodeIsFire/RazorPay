"""CLI wrapper: writes the deterministic synthetic dataset to data/fixtures/
as JSON, so it can be inspected by hand or loaded into the DB separately
from the app. Re-running is safe — output is byte-identical (fixed seed).

Usage: python -m scripts.generate_fixtures
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.fixtures import generate_dataset  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_rows, gateway_rows, ground_truth = generate_dataset()

    (OUT_DIR / "ledger.json").write_text(json.dumps(ledger_rows, indent=2))
    (OUT_DIR / "gateway.json").write_text(json.dumps(gateway_rows, indent=2))
    (OUT_DIR / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2))

    print(f"Wrote {len(ledger_rows)} ledger rows, {len(gateway_rows)} gateway rows "
          f"-> {OUT_DIR}")
    print("Case summary:", ground_truth["summary"])


if __name__ == "__main__":
    main()
