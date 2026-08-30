import sqlite3
from pathlib import Path

from app.fixtures import generate_dataset
from app.load_fixtures import load_dataset_into_db
from app.reconcile import reconcile, reconcile_from_db

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


# ---------------------------------------------------------------------------
# Correctness against the M1 fixture's ground truth
# ---------------------------------------------------------------------------

def test_matched_count_equals_exact_match_plus_duplicate_cases():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)
    # every exact_match case matches (30), and every duplicate case's
    # ledger row matches its earliest gateway row (6) -> 36 total
    assert len(result.matches) == 36


def test_unmatched_ledger_is_exactly_fee_timing_and_failed_cases():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)

    expected_refs = {
        case["ledger_ref"] for case in gt["cases"]
        if case["case_type"] in ("fee_mismatch", "timing_lag", "failed_payment")
    }
    actual_refs = {r["external_ref"] for r in result.unmatched_ledger}
    assert actual_refs == expected_refs
    assert len(result.unmatched_ledger) == 24


def test_unmatched_gateway_is_fee_timing_gateway_rows_plus_duplicate_surplus():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)

    expected_refs = set()
    for case in gt["cases"]:
        if case["case_type"] in ("fee_mismatch", "timing_lag", "unexplained"):
            expected_refs.update(case["gateway_refs"])
        elif case["case_type"] == "duplicate":
            # the later of the two gateway rows is the unmatched surplus
            gw_rows = [g for g in gateway_rows if g["external_ref"] in case["gateway_refs"]]
            later = max(gw_rows, key=lambda g: g["occurred_at"])
            expected_refs.add(later["external_ref"])

    actual_refs = {r["external_ref"] for r in result.unmatched_gateway}
    assert actual_refs == expected_refs
    assert len(result.unmatched_gateway) == 24


def test_duplicate_case_matches_the_earlier_gateway_row():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)

    dup_cases = [c for c in gt["cases"] if c["case_type"] == "duplicate"]
    assert len(dup_cases) == 6

    matched_gateway_refs = {gw["external_ref"] for _, gw, _ in result.matches}
    for case in dup_cases:
        gw_rows = [g for g in gateway_rows if g["external_ref"] in case["gateway_refs"]]
        earlier = min(gw_rows, key=lambda g: g["occurred_at"])
        later = max(gw_rows, key=lambda g: g["occurred_at"])
        assert earlier["external_ref"] in matched_gateway_refs
        assert later["external_ref"] not in matched_gateway_refs


def test_all_exact_match_cases_matched_at_tier_exact():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)

    exact_ledger_refs = {
        c["ledger_ref"] for c in gt["cases"] if c["case_type"] == "exact_match"
    }
    for led, gw, tier in result.matches:
        if led["external_ref"] in exact_ledger_refs:
            assert tier == "exact"


def test_no_gateway_row_matched_twice():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)
    matched_gw_refs = [gw["external_ref"] for _, gw, _ in result.matches]
    assert len(matched_gw_refs) == len(set(matched_gw_refs))


# ---------------------------------------------------------------------------
# Fuzzy-tier behavior the M1 fixture doesn't exercise (every fixture gateway
# row already carries a reference_id) — hand-built minimal cases instead.
# ---------------------------------------------------------------------------

def _row(source, ref, amount, counterparty, occurred_at, reference_id=None):
    return {
        "source": source, "external_ref": ref, "reference_id": reference_id,
        "amount_paise": amount, "currency": "INR", "counterparty": counterparty,
        "narration": "", "occurred_at": occurred_at, "raw_json": "{}",
    }


def test_fuzzy_match_on_small_rounding_difference():
    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [_row("gateway", "G1", 100_050, "Vendor A", "2026-01-01T10:30:00+00:00", reference_id="L1")]
    result = reconcile(ledger, gateway)
    assert len(result.matches) == 1
    assert result.matches[0][2] == "fuzzy"
    assert not result.unmatched_ledger and not result.unmatched_gateway


def test_fuzzy_match_by_counterparty_when_reference_id_missing():
    ledger = [_row("ledger", "L1", 50_000, "Vendor B", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [_row("gateway", "G1", 50_000, "Vendor B", "2026-01-01T10:15:00+00:00", reference_id=None)]
    result = reconcile(ledger, gateway)
    assert len(result.matches) == 1
    assert result.matches[0][2] == "fuzzy"


def test_fuzzy_tier_still_respects_amount_tolerance():
    """A gap bigger than the tolerance must NOT be waved through as fuzzy —
    that's what would make a real fee mismatch invisible."""
    ledger = [_row("ledger", "L1", 100_000, "Vendor C", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [_row("gateway", "G1", 99_000, "Vendor C", "2026-01-01T10:15:00+00:00", reference_id="L1")]
    result = reconcile(ledger, gateway)
    assert len(result.matches) == 0
    assert len(result.unmatched_ledger) == 1
    assert len(result.unmatched_gateway) == 1


# ---------------------------------------------------------------------------
# DB-facing wrapper
# ---------------------------------------------------------------------------

def test_reconcile_from_db_matches_pure_function_and_logs_audit():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)

    result = reconcile_from_db(conn)
    assert len(result.matches) == 36

    audit_rows = conn.execute(
        "SELECT * FROM audit_log WHERE actor='reconciler'"
    ).fetchall()
    assert len(audit_rows) == 1
    assert audit_rows[0]["event"] == "reconciliation_complete"
    conn.close()


# ---------------------------------------------------------------------------
# Multi-source: reconciling the ledger against something other than
# 'gateway' (e.g. a real bank statement), same matcher, different table filter.
# ---------------------------------------------------------------------------

def test_reconcile_from_db_can_target_bank_statement_source():
    conn = _fresh_db()
    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    # A gateway row exists for L1, but no bank_statement row does -- only
    # the bank_statement pass should see L1 as unmatched.
    gateway = [_row("gateway", "G1", 100_000, "Vendor A", "2026-01-01T10:05:00+00:00", reference_id="L1")]
    load_dataset_into_db(conn, ledger, gateway)

    gw_result = reconcile_from_db(conn, actual_source="gateway")
    assert len(gw_result.matches) == 1
    assert not gw_result.unmatched_ledger

    bank_result = reconcile_from_db(conn, actual_source="bank_statement")
    assert not bank_result.matches
    assert [r["external_ref"] for r in bank_result.unmatched_ledger] == ["L1"]
    conn.close()


def test_reconcile_from_db_rejects_an_unknown_actual_source():
    conn = _fresh_db()
    try:
        reconcile_from_db(conn, actual_source="erp_export")
        assert False, "expected ValueError"
    except ValueError:
        pass
    conn.close()
