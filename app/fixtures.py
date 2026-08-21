"""Deterministic synthetic data for the reconciliation engine.

generate_dataset() produces two lists of row-dicts shaped exactly like the
`transactions` table (source='ledger' vs source='gateway'), plus a
ground_truth structure describing what SHOULD happen to each case. That
ground truth is the whole point: M2 and M3 assert against it directly
instead of eyeballing counts, so "the matcher looks right" becomes "the
matcher provably matches N cases and misses exactly these M for these
reasons."

Five case types, matching the four exception causes from the architecture
doc plus the trivial exact-match case:

  exact_match     ledger + gateway agree on reference_id, amount, timing
  fee_mismatch    same reference_id, gateway amount = ledger amount - fee
  duplicate       one ledger entry, two gateway entries (a retried payout)
  timing_lag      same reference_id + amount, gateway far outside the
                  matcher's time window (simulates T+3 "deemed success")
  failed_payment  ledger entry with no gateway counterpart at all

Everything is seeded and anchored to a fixed date, so re-running this
produces byte-identical output — required for the tests to stay meaningful.
"""
import json
import random
from datetime import datetime, timedelta, timezone

SEED = 42
ANCHOR = datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)

VENDORS = [
    "Acme Traders", "Bharat Logistics", "Crimson Supplies", "Delta Freight",
    "Everest Textiles", "Falcon Components", "Ganges Packaging",
    "Horizon Electricals", "Indus Hardware", "Jupiter Chemicals",
    "Konkan Foods", "Lotus Stationery", "Malabar Spices", "Nilgiri Tea Co",
    "Orion Fabrics", "Pallavi Prints", "Quartz Metals", "Ridgeline Tools",
    "Sundar Furnishings", "Tricolor Paints",
]

CASE_PLAN = [
    ("exact_match", 10),
    ("fee_mismatch", 3),
    ("duplicate", 2),
    ("timing_lag", 2),
    ("failed_payment", 3),
]


def _rupees_to_paise(rupees: float) -> int:
    return int(round(rupees * 100))


def _fake_payout_id(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "pout_" + "".join(rng.choice(alphabet) for _ in range(14))


def _fake_utr(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789") for _ in range(12))


def generate_dataset():
    rng = random.Random(SEED)
    ledger_rows = []
    gateway_rows = []
    cases = []

    seq = 0
    vendor_cursor = 0
    time_cursor = ANCHOR

    for case_type, count in CASE_PLAN:
        for _ in range(count):
            seq += 1
            ledger_id = f"LED-{seq:04d}"
            vendor = VENDORS[vendor_cursor % len(VENDORS)]
            vendor_cursor += 1
            amount_rupees = rng.randint(1_000, 50_000)
            amount_paise = _rupees_to_paise(amount_rupees)
            expected_at = time_cursor
            time_cursor += timedelta(hours=rng.randint(2, 6))

            ledger_rows.append({
                "source": "ledger",
                "external_ref": ledger_id,
                "reference_id": ledger_id,
                "amount_paise": amount_paise,
                "currency": "INR",
                "counterparty": vendor,
                "narration": f"Payout to {vendor}",
                "occurred_at": expected_at.isoformat(),
                "raw_json": json.dumps({
                    "kind": "ledger_entry",
                    "order_id": ledger_id,
                    "vendor": vendor,
                    "expected_amount_inr": amount_rupees,
                }),
            })

            case = {
                "case_type": case_type,
                "ledger_ref": ledger_id,
                "gateway_refs": [],
            }

            if case_type == "exact_match":
                gw_id = _fake_payout_id(rng)
                gw_time = expected_at + timedelta(minutes=rng.randint(5, 90))
                gateway_rows.append({
                    "source": "gateway",
                    "external_ref": gw_id,
                    "reference_id": ledger_id,
                    "amount_paise": amount_paise,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": gw_time.isoformat(),
                    "raw_json": json.dumps({
                        "kind": "razorpayx_transaction", "id": gw_id,
                        "reference_id": ledger_id, "status": "processed",
                        "utr": _fake_utr(rng), "fees": 0, "tax": 0,
                    }),
                })
                case["gateway_refs"] = [gw_id]

            elif case_type == "fee_mismatch":
                gw_id = _fake_payout_id(rng)
                gw_time = expected_at + timedelta(minutes=rng.randint(5, 90))
                fee_paise = _rupees_to_paise(rng.choice([2, 3, 5, 9, 10]))
                net_amount = amount_paise - fee_paise
                gateway_rows.append({
                    "source": "gateway",
                    "external_ref": gw_id,
                    "reference_id": ledger_id,
                    "amount_paise": net_amount,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": gw_time.isoformat(),
                    "raw_json": json.dumps({
                        "kind": "razorpayx_transaction", "id": gw_id,
                        "reference_id": ledger_id, "status": "processed",
                        "utr": _fake_utr(rng), "fees": fee_paise, "tax": 0,
                    }),
                })
                case["gateway_refs"] = [gw_id]
                case["fee_paise"] = fee_paise

            elif case_type == "duplicate":
                refs = []
                for _ in range(2):
                    gw_id = _fake_payout_id(rng)
                    gw_time = expected_at + timedelta(minutes=rng.randint(5, 90))
                    gateway_rows.append({
                        "source": "gateway",
                        "external_ref": gw_id,
                        "reference_id": ledger_id,
                        "amount_paise": amount_paise,
                        "currency": "INR",
                        "counterparty": vendor,
                        "narration": f"Payout to {vendor}",
                        "occurred_at": gw_time.isoformat(),
                        "raw_json": json.dumps({
                            "kind": "razorpayx_transaction", "id": gw_id,
                            "reference_id": ledger_id, "status": "processed",
                            "utr": _fake_utr(rng), "fees": 0, "tax": 0,
                        }),
                    })
                    refs.append(gw_id)
                case["gateway_refs"] = refs

            elif case_type == "timing_lag":
                gw_id = _fake_payout_id(rng)
                gw_time = expected_at + timedelta(days=rng.randint(5, 8))
                gateway_rows.append({
                    "source": "gateway",
                    "external_ref": gw_id,
                    "reference_id": ledger_id,
                    "amount_paise": amount_paise,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": gw_time.isoformat(),
                    "raw_json": json.dumps({
                        "kind": "razorpayx_transaction", "id": gw_id,
                        "reference_id": ledger_id, "status": "processed",
                        "utr": _fake_utr(rng), "fees": 0, "tax": 0,
                    }),
                })
                case["gateway_refs"] = [gw_id]

            elif case_type == "failed_payment":
                pass  # ledger row stands alone, no gateway counterpart

            cases.append(case)

    summary = {}
    for case_type, count in CASE_PLAN:
        summary[case_type] = count

    ground_truth = {
        "seed": SEED,
        "anchor": ANCHOR.isoformat(),
        "summary": summary,
        "cases": cases,
        "totals": {
            "ledger_rows": len(ledger_rows),
            "gateway_rows": len(gateway_rows),
        },
    }
    return ledger_rows, gateway_rows, ground_truth
