import sqlite3
from pathlib import Path

from app.fixtures import CASE_PLAN, generate_dataset
from app.load_fixtures import load_dataset_into_db

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def test_generation_is_deterministic():
    ledger1, gateway1, gt1 = generate_dataset()
    ledger2, gateway2, gt2 = generate_dataset()
    assert ledger1 == ledger2
    assert gateway1 == gateway2
    assert gt1 == gt2


def test_case_plan_totals_match_row_counts():
    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    expected_ledger_count = sum(count for _, count in CASE_PLAN)
    assert len(ledger_rows) == expected_ledger_count
    assert len(ground_truth["cases"]) == expected_ledger_count
    # every case type appears exactly as many times as planned
    seen = {}
    for case in ground_truth["cases"]:
        seen[case["case_type"]] = seen.get(case["case_type"], 0) + 1
    assert seen == dict(CASE_PLAN)


def test_gateway_row_count_matches_case_shapes():
    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    # exact_match/fee_mismatch/timing_lag -> 1 gateway row each,
    # duplicate -> 2, failed_payment -> 0
    expected = {
        "exact_match": 1, "fee_mismatch": 1, "duplicate": 2,
        "timing_lag": 1, "failed_payment": 0,
    }
    expected_total = sum(
        expected[case["case_type"]] for case in ground_truth["cases"]
    )
    assert len(gateway_rows) == expected_total
    for case in ground_truth["cases"]:
        assert len(case["gateway_refs"]) == expected[case["case_type"]]


def test_loads_cleanly_into_schema():
    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)

    ledger_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source='ledger'"
    ).fetchone()[0]
    gateway_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source='gateway'"
    ).fetchone()[0]

    assert ledger_count == ground_truth["totals"]["ledger_rows"]
    assert gateway_count == ground_truth["totals"]["gateway_rows"]
    conn.close()


def test_fee_mismatch_rows_actually_differ_in_amount():
    """Sanity check the fixture logic itself, not just counts: a
    fee_mismatch case's gateway amount must be strictly less than the
    ledger amount, by the recorded fee -- otherwise M2/M3 would be built
    against a fixture that doesn't exercise the case it claims to."""
    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    ledger_by_ref = {r["external_ref"]: r for r in ledger_rows}
    gateway_by_ref = {r["external_ref"]: r for r in gateway_rows}

    for case in ground_truth["cases"]:
        if case["case_type"] != "fee_mismatch":
            continue
        ledger_amount = ledger_by_ref[case["ledger_ref"]]["amount_paise"]
        gw_amount = gateway_by_ref[case["gateway_refs"][0]]["amount_paise"]
        assert gw_amount == ledger_amount - case["fee_paise"]
        assert gw_amount < ledger_amount


def test_timing_lag_rows_fall_outside_default_fuzzy_window():
    from datetime import datetime

    from app.config import FUZZY_TIME_WINDOW_HOURS

    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    ledger_by_ref = {r["external_ref"]: r for r in ledger_rows}
    gateway_by_ref = {r["external_ref"]: r for r in gateway_rows}

    for case in ground_truth["cases"]:
        if case["case_type"] != "timing_lag":
            continue
        led_time = datetime.fromisoformat(ledger_by_ref[case["ledger_ref"]]["occurred_at"])
        gw_time = datetime.fromisoformat(gateway_by_ref[case["gateway_refs"][0]]["occurred_at"])
        delta_hours = abs((gw_time - led_time).total_seconds()) / 3600
        assert delta_hours > FUZZY_TIME_WINDOW_HOURS
