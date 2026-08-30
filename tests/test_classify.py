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

    assert len(exceptions) == 33

    by_cause = {}
    for e in exceptions:
        by_cause.setdefault(e.cause, []).append(e)

    assert len(by_cause.get("fee_mismatch", [])) == 9
    assert len(by_cause.get("timing_lag", [])) == 6
    assert len(by_cause.get("failed_payment", [])) == 9
    assert len(by_cause.get("duplicate", [])) == 6
    assert len(by_cause.get("unexplained", [])) == 3  # the orphan gateway rows

    expected_ledger_refs_by_cause = {"fee_mismatch": set(), "timing_lag": set(),
                                      "failed_payment": set(), "duplicate": set()}
    for case in gt["cases"]:
        if case["case_type"] in expected_ledger_refs_by_cause:
            expected_ledger_refs_by_cause[case["case_type"]].add(case["ledger_ref"])

    for cause, expected_refs in expected_ledger_refs_by_cause.items():
        actual_refs = {e.ledger_ref for e in by_cause[cause]}
        assert actual_refs == expected_refs, f"mismatch for cause={cause}"

    # unexplained cases have no ledger row at all -- every exception's
    # ledger_ref is None, and its gateway_ref matches the fixture's orphan rows
    expected_orphan_gw_refs = {
        case["gateway_refs"][0] for case in gt["cases"] if case["case_type"] == "unexplained"
    }
    assert all(e.ledger_ref is None for e in by_cause["unexplained"])
    assert {e.gateway_ref for e in by_cause["unexplained"]} == expected_orphan_gw_refs


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
# fee_mismatch auto-resolution: a delta within the known fee schedule is
# explained, not a dispute -- see classify.py / KNOWN_FEE_TOLERANCE_PAISE.
# ---------------------------------------------------------------------------

def test_small_fee_delta_is_auto_resolved_not_flagged_for_a_human():
    from app.config import KNOWN_FEE_TOLERANCE_PAISE

    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [_row("gateway", "G1", 100_000 - KNOWN_FEE_TOLERANCE_PAISE, "Vendor A",
                     "2026-01-01T10:15:00+00:00", reference_id="L1")]
    result = reconcile(ledger, gateway)
    exceptions = classify(result)
    assert len(exceptions) == 1
    assert exceptions[0].cause == "fee_mismatch"
    assert exceptions[0].auto_resolve is True


def test_large_fee_delta_still_needs_a_human():
    from app.config import KNOWN_FEE_TOLERANCE_PAISE

    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [_row("gateway", "G1", 100_000 - KNOWN_FEE_TOLERANCE_PAISE - 100, "Vendor A",
                     "2026-01-01T10:15:00+00:00", reference_id="L1")]
    result = reconcile(ledger, gateway)
    exceptions = classify(result)
    assert len(exceptions) == 1
    assert exceptions[0].cause == "fee_mismatch"
    assert exceptions[0].auto_resolve is False


def test_auto_resolved_fee_mismatch_lands_as_resolved_in_the_db():
    from app.config import KNOWN_FEE_TOLERANCE_PAISE

    ledger_rows = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway_rows = [_row("gateway", "G1", 100_000 - KNOWN_FEE_TOLERANCE_PAISE, "Vendor A",
                          "2026-01-01T10:15:00+00:00", reference_id="L1")]
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)

    new_keys = classify_and_persist_from_db(conn)
    assert len(new_keys) == 1

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (new_keys[0],)).fetchone()
    assert exc["status"] == "resolved"

    audit = conn.execute(
        "SELECT * FROM audit_log WHERE event='exception_auto_resolved'"
    ).fetchone()
    assert audit is not None
    assert audit["actor"] == "classifier"

    conn.close()


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
# Multi-source: classify() tagging exceptions with which non-ledger source
# produced them, and staying collision-free across two separate passes.
# ---------------------------------------------------------------------------

def test_classify_tags_exceptions_with_the_matched_source():
    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    result = reconcile(ledger, [])
    exceptions = classify(result, source="bank_statement")
    assert len(exceptions) == 1
    assert exceptions[0].cause == "failed_payment"
    assert exceptions[0].matched_source == "bank_statement"


def test_failed_payment_keys_dont_collide_across_sources():
    """The same unmatched ledger row, reconciled separately against two
    different actual sources, must produce two distinct exception rows --
    not one silently overwritten by ON CONFLICT DO NOTHING."""
    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    result = reconcile(ledger, [])

    gw_exceptions = classify(result, source="gateway")
    bank_exceptions = classify(result, source="bank_statement")

    assert gw_exceptions[0].exception_key != bank_exceptions[0].exception_key


def test_fee_mismatch_auto_resolve_is_gateway_only():
    """KNOWN_FEE_TOLERANCE_PAISE encodes RazorpayX's own fee schedule --
    the same small delta against a bank statement has no such explanation
    and must still need a human."""
    from app.config import KNOWN_FEE_TOLERANCE_PAISE

    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    bank = [_row("bank_statement", "B1", 100_000 - KNOWN_FEE_TOLERANCE_PAISE, "Vendor A",
                  "2026-01-01T10:15:00+00:00", reference_id="L1")]
    result = reconcile(ledger, bank)
    exceptions = classify(result, source="bank_statement")
    assert len(exceptions) == 1
    assert exceptions[0].cause == "fee_mismatch"
    assert exceptions[0].auto_resolve is False


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
    assert len(new_keys_1) == 33

    row_count = conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0]
    assert row_count == 33

    audit_count = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event='exception_created'"
    ).fetchone()[0]
    assert audit_count == 33

    # Re-running must not duplicate rows or re-log already-known exceptions.
    new_keys_2 = persist_exceptions(conn, exceptions)
    conn.commit()
    assert new_keys_2 == []
    assert conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0] == 33
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event='exception_created'"
    ).fetchone()[0] == 33

    conn.close()


def test_classify_and_persist_from_db_full_pipeline():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)

    new_keys = classify_and_persist_from_db(conn)
    assert len(new_keys) == 33

    # 9 fee_mismatch cases auto-resolve immediately (delta is within the
    # known fee tolerance -- see classify.py) and never touch 'open' at
    # all; the rest do start open for M5 to move forward.
    open_count = conn.execute(
        "SELECT COUNT(*) FROM exceptions WHERE status='open'"
    ).fetchone()[0]
    assert open_count == 24

    resolved_count = conn.execute(
        "SELECT COUNT(*) FROM exceptions WHERE status='resolved'"
    ).fetchone()[0]
    assert resolved_count == 9

    conn.close()
