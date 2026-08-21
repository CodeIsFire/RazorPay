import sqlite3
from pathlib import Path

from app.classify import classify, classify_and_persist_from_db, persist_exceptions
from app.fixtures import generate_dataset
from app.load_fixtures import load_dataset_into_db
from app.reconcile import reconcile, reconcile_from_db

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _row(source, ref, amount, counterparty, occurred_at, reference_id=None):
    return {
        "source": source, "external_ref": ref, "reference_id": reference_id,
        "amount_paise": amount, "currency": "INR", "counterparty": counterparty,
        "narration": "", "occurred_at": occurred_at, "raw_json": "{}",
    }


# ---------------------------------------------------------------------------
# Correctness against the M1 fixture's ground truth
# ---------------------------------------------------------------------------

def test_classification_counts_and_causes_match_ground_truth():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)
    exceptions = classify(result)

    assert len(exceptions) == 10

    by_cause = {}
    for e in exceptions:
        by_cause.setdefault(e.cause, []).append(e)

    assert len(by_cause.get("fee_mismatch", [])) == 3
    assert len(by_cause.get("timing_lag", [])) == 2
    assert len(by_cause.get("failed_payment", [])) == 3
    assert len(by_cause.get("duplicate", [])) == 2
    assert "unexplained" not in by_cause  # fixture shouldn't produce any

    expected_ledger_refs_by_cause = {"fee_mismatch": set(), "timing_lag": set(),
                                      "failed_payment": set(), "duplicate": set()}
    for case in gt["cases"]:
        if case["case_type"] in expected_ledger_refs_by_cause:
            expected_ledger_refs_by_cause[case["case_type"]].add(case["ledger_ref"])

    for cause, expected_refs in expected_ledger_refs_by_cause.items():
        actual_refs = {e.ledger_ref for e in by_cause[cause]}
        assert actual_refs == expected_refs, f"mismatch for cause={cause}"


def test_exception_keys_are_unique():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)
    exceptions = classify(result)
    keys = [e.exception_key for e in exceptions]
    assert len(keys) == len(set(keys))


def test_duplicate_exception_references_the_later_gateway_row():
    ledger_rows, gateway_rows, gt = generate_dataset()
    result = reconcile(ledger_rows, gateway_rows)
    exceptions = classify(result)

    dup_exceptions = {e.ledger_ref: e for e in exceptions if e.cause == "duplicate"}
    for case in gt["cases"]:
        if case["case_type"] != "duplicate":
            continue
        gw_rows = [g for g in gateway_rows if g["external_ref"] in case["gateway_refs"]]
        later = max(gw_rows, key=lambda g: g["occurred_at"])
        assert dup_exceptions[case["ledger_ref"]].gateway_ref == later["external_ref"]


# ---------------------------------------------------------------------------
# Edge case the fixture doesn't cover
# ---------------------------------------------------------------------------

def test_orphan_gateway_row_is_unexplained():
    gateway = [_row("gateway", "G-ORPHAN", 42_000, "Nobody", "2026-01-01T00:00:00+00:00",
                     reference_id="LED-DOES-NOT-EXIST")]
    result = reconcile([], gateway)
    exceptions = classify(result)
    assert len(exceptions) == 1
    assert exceptions[0].cause == "unexplained"
    assert exceptions[0].gateway_ref == "G-ORPHAN"
    assert exceptions[0].ledger_ref is None


# ---------------------------------------------------------------------------
# Persistence: idempotent insert + audit trail
# ---------------------------------------------------------------------------

def test_persist_exceptions_is_idempotent_and_audited():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)

    result = reconcile_from_db(conn)
    exceptions = classify(result)

    new_keys_1 = persist_exceptions(conn, exceptions)
    conn.commit()
    assert len(new_keys_1) == 10

    row_count = conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0]
    assert row_count == 10

    audit_count = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event='exception_created'"
    ).fetchone()[0]
    assert audit_count == 10

    # Re-running must not duplicate rows or re-log already-known exceptions.
    new_keys_2 = persist_exceptions(conn, exceptions)
    conn.commit()
    assert new_keys_2 == []
    assert conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0] == 10
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event='exception_created'"
    ).fetchone()[0] == 10

    conn.close()


def test_classify_and_persist_from_db_full_pipeline():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)

    new_keys = classify_and_persist_from_db(conn)
    assert len(new_keys) == 10

    open_count = conn.execute(
        "SELECT COUNT(*) FROM exceptions WHERE status='open'"
    ).fetchone()[0]
    assert open_count == 10  # everything starts open; M5 will move status forward

    conn.close()
