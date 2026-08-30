"""Split-payment (1 ledger -> N gateway) and batch-settlement (N ledger ->
1 gateway) matching, plus the 'partial_payment' cause for groups that
haven't fully summed up yet. See docs/superpowers/specs -- brainstormed
and approved in chat across several sections before implementation."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _row(source, ref, amount, counterparty, occurred_at, reference_id=None,
         settlement_batch_id=None):
    return {
        "source": source, "external_ref": ref, "reference_id": reference_id,
        "amount_paise": amount, "currency": "INR", "counterparty": counterparty,
        "narration": "", "occurred_at": occurred_at, "raw_json": "{}",
        "settlement_batch_id": settlement_batch_id,
    }


# ---------------------------------------------------------------------------
# Schema: settlement_batch_id column + partial_payment cause
# ---------------------------------------------------------------------------

def test_schema_accepts_settlement_batch_id_on_transactions():
    conn = _fresh_db()
    conn.execute(
        """INSERT INTO transactions
               (source, external_ref, reference_id, amount_paise, occurred_at, settlement_batch_id)
           VALUES ('ledger', 'L1', 'L1', 1000, '2026-01-01T00:00:00+00:00', 'BATCH-1')"""
    )
    row = conn.execute("SELECT settlement_batch_id FROM transactions WHERE external_ref='L1'").fetchone()
    assert row["settlement_batch_id"] == "BATCH-1"
    conn.close()


def test_schema_accepts_partial_payment_cause_on_exceptions():
    conn = _fresh_db()
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, amount_paise, matched_source)
           VALUES ('partial_payment:split:L1:-', 'partial_payment', 1000, 'gateway')"""
    )
    row = conn.execute("SELECT cause FROM exceptions WHERE exception_key='partial_payment:split:L1:-'").fetchone()
    assert row["cause"] == "partial_payment"
    conn.close()


# ---------------------------------------------------------------------------
# reconcile(): split pass -- 1 ledger row disbursed across N gateway rows
# ---------------------------------------------------------------------------

def test_split_pass_claims_gateway_rows_summing_to_the_ledger_amount():
    from app.reconcile import reconcile

    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [
        _row("gateway", "G1", 60_000, "Vendor A", "2026-01-01T10:05:00+00:00", reference_id="L1"),
        _row("gateway", "G2", 40_000, "Vendor A", "2026-01-01T10:10:00+00:00", reference_id="L1"),
    ]
    result = reconcile(ledger, gateway)

    assert not result.matches
    assert not result.unmatched_ledger
    assert not result.unmatched_gateway
    assert not result.partial_groups
    assert len(result.split_matches) == 1

    split = result.split_matches[0]
    assert split["ledger"]["external_ref"] == "L1"
    assert {g["external_ref"] for g in split["gateways"]} == {"G1", "G2"}


def test_split_pass_leaves_a_partial_group_when_the_sum_is_short():
    from app.reconcile import reconcile

    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    # Only 1 of an eventual 2-3 pieces has arrived so far.
    gateway = [
        _row("gateway", "G1", 30_000, "Vendor A", "2026-01-01T10:05:00+00:00", reference_id="L1"),
        _row("gateway", "G2", 20_000, "Vendor A", "2026-01-01T10:10:00+00:00", reference_id="L1"),
    ]
    result = reconcile(ledger, gateway)

    assert not result.split_matches
    assert not result.matches
    # Claimed nowhere else -- still visible as unmatched too, since nothing
    # has actually resolved this ledger row yet.
    assert len(result.partial_groups) == 1
    group = result.partial_groups[0]
    assert group["kind"] == "split"
    assert group["ledgers"][0]["external_ref"] == "L1"
    assert {g["external_ref"] for g in group["gateways"]} == {"G1", "G2"}


def test_single_candidate_with_no_full_match_is_not_treated_as_a_split():
    """Exactly one candidate sharing a reference_id is today's fee_mismatch/
    timing_lag/unexplained territory (classify.py), not a split -- the split
    pass must only activate for 2+ candidates."""
    from app.reconcile import reconcile

    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [_row("gateway", "G1", 50_000, "Vendor A", "2026-01-01T10:05:00+00:00", reference_id="L1")]
    result = reconcile(ledger, gateway)

    assert not result.split_matches
    assert not result.partial_groups
    assert len(result.unmatched_ledger) == 1
    assert len(result.unmatched_gateway) == 1


def test_duplicate_full_amount_payout_is_not_misread_as_a_split():
    """A genuine duplicate (second gateway row repeats the FULL ledger
    amount) must still be caught by the existing exact-match tier and left
    for classify.py's 'duplicate' cause -- the split pass must never see it."""
    from app.reconcile import reconcile

    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    gateway = [
        _row("gateway", "G1", 100_000, "Vendor A", "2026-01-01T10:05:00+00:00", reference_id="L1"),
        _row("gateway", "G2", 100_000, "Vendor A", "2026-01-01T10:10:00+00:00", reference_id="L1"),
    ]
    result = reconcile(ledger, gateway)

    assert len(result.matches) == 1
    assert result.matches[0][2] == "exact"
    assert not result.split_matches
    assert not result.partial_groups
    assert len(result.unmatched_gateway) == 1  # the surplus dup, for classify.py to flag


# ---------------------------------------------------------------------------
# reconcile(): batch pass -- N ledger rows settled by 1 gateway row
# ---------------------------------------------------------------------------

def test_batch_pass_claims_ledger_rows_sharing_a_settlement_batch_id():
    from app.reconcile import reconcile

    ledger = [
        _row("ledger", "L1", 60_000, "Vendor A", "2026-01-01T10:00:00+00:00",
             reference_id="L1", settlement_batch_id="BATCH-1"),
        _row("ledger", "L2", 40_000, "Vendor A", "2026-01-01T10:00:00+00:00",
             reference_id="L2", settlement_batch_id="BATCH-1"),
    ]
    gateway = [_row("gateway", "G1", 100_000, "Vendor A", "2026-01-01T10:15:00+00:00",
                     reference_id="BATCH-1")]
    result = reconcile(ledger, gateway)

    assert not result.matches
    assert not result.unmatched_ledger
    assert not result.unmatched_gateway
    assert not result.partial_groups
    assert len(result.batch_matches) == 1

    batch = result.batch_matches[0]
    assert batch["gateway"]["external_ref"] == "G1"
    assert {l["external_ref"] for l in batch["ledgers"]} == {"L1", "L2"}


def test_batch_pass_leaves_a_partial_group_when_the_sum_is_short():
    from app.reconcile import reconcile

    ledger = [
        _row("ledger", "L1", 60_000, "Vendor A", "2026-01-01T10:00:00+00:00",
             reference_id="L1", settlement_batch_id="BATCH-1"),
        _row("ledger", "L2", 40_000, "Vendor A", "2026-01-01T10:00:00+00:00",
             reference_id="L2", settlement_batch_id="BATCH-1"),
    ]
    # The batch settlement only covers L1 so far -- L2's share hasn't landed.
    gateway = [_row("gateway", "G1", 60_000, "Vendor A", "2026-01-01T10:15:00+00:00",
                     reference_id="BATCH-1")]
    result = reconcile(ledger, gateway)

    assert not result.batch_matches
    assert len(result.partial_groups) == 1
    group = result.partial_groups[0]
    assert group["kind"] == "batch"
    assert {l["external_ref"] for l in group["ledgers"]} == {"L1", "L2"}
    assert group["gateways"][0]["external_ref"] == "G1"


# ---------------------------------------------------------------------------
# classify(): split/batch matches produce no exceptions; partial_groups
# produce exactly one 'partial_payment' exception each.
# ---------------------------------------------------------------------------

def _led(ref, amount, occurred_at="2026-01-01T10:00:00+00:00"):
    return {"external_ref": ref, "amount_paise": amount, "occurred_at": occurred_at,
            "reference_id": ref, "counterparty": "Vendor A"}


def _gw(ref, amount, occurred_at="2026-01-01T10:05:00+00:00", reference_id=None):
    return {"external_ref": ref, "amount_paise": amount, "occurred_at": occurred_at,
            "reference_id": reference_id}


def test_split_and_batch_matches_produce_no_exceptions():
    from app.classify import classify
    from app.reconcile import MatchResult

    result = MatchResult(
        split_matches=[{"ledger": _led("L1", 100_000), "gateways": [_gw("G1", 60_000), _gw("G2", 40_000)]}],
        batch_matches=[{"ledgers": [_led("L2", 60_000), _led("L3", 40_000)], "gateway": _gw("G3", 100_000)}],
    )
    exceptions = classify(result)
    assert exceptions == []


def test_split_partial_group_produces_one_partial_payment_exception():
    from app.classify import classify

    class FakeResult:
        matches = []
        split_matches = []
        batch_matches = []
        unmatched_ledger = []
        unmatched_gateway = []
        partial_groups = [{
            "kind": "split",
            "ledgers": [_led("L1", 100_000)],
            "gateways": [_gw("G1", 30_000), _gw("G2", 20_000)],
        }]

    exceptions = classify(FakeResult())
    assert len(exceptions) == 1
    exc = exceptions[0]
    assert exc.cause == "partial_payment"
    assert exc.exception_key == "partial_payment:split:L1:-"
    assert exc.ledger_ref == "L1"
    assert exc.gateway_ref == "G1,G2"
    assert exc.amount_paise == 100_000
    assert exc.auto_resolve is False


def test_batch_partial_group_joins_multiple_ledger_refs():
    from app.classify import classify

    class FakeResult:
        matches = []
        split_matches = []
        batch_matches = []
        unmatched_ledger = []
        unmatched_gateway = []
        partial_groups = [{
            "kind": "batch",
            "ledgers": [_led("L2", 40_000), _led("L1", 60_000)],  # deliberately out of order
            "gateways": [_gw("G1", 60_000, reference_id="BATCH-1")],
        }]

    exceptions = classify(FakeResult())
    assert len(exceptions) == 1
    exc = exceptions[0]
    assert exc.cause == "partial_payment"
    assert exc.exception_key == "partial_payment:batch:BATCH-1:-"
    assert exc.ledger_ref == "L1,L2"  # sorted, not insertion order
    assert exc.gateway_ref == "G1"
    assert exc.amount_paise == 100_000


def test_partial_payment_action_maps_to_send_reminder():
    from app.router import ACTION_MAP
    assert ACTION_MAP["partial_payment"] == "send_reminder"


# ---------------------------------------------------------------------------
# persist_exceptions(): partial_payment updates in place instead of being
# skipped on conflict, but only while still open/pending.
# ---------------------------------------------------------------------------

def _partial_exception(gateway_refs, amount_paise, detail="short so far"):
    from app.classify import Exception_
    return Exception_(
        exception_key="partial_payment:split:L1:-", cause="partial_payment",
        ledger_ref="L1", gateway_ref=",".join(gateway_refs),
        amount_paise=amount_paise, detail=detail,
    )


def test_persist_exceptions_updates_an_open_partial_payment_on_rerun():
    from app.classify import persist_exceptions
    conn = _fresh_db()

    new_keys_1 = persist_exceptions(conn, [_partial_exception(["G1"], 100_000, "1 of ~3 pieces found")])
    conn.commit()
    assert new_keys_1 == ["partial_payment:split:L1:-"]

    new_keys_2 = persist_exceptions(conn, [_partial_exception(["G1", "G2"], 100_000, "2 of ~3 pieces found")])
    conn.commit()
    assert new_keys_2 == []  # not "new" -- it's an update to an existing row

    row = conn.execute(
        "SELECT gateway_ref, detail FROM exceptions WHERE exception_key='partial_payment:split:L1:-'"
    ).fetchone()
    assert row["gateway_ref"] == "G1,G2"
    assert row["detail"] == "2 of ~3 pieces found"

    count = conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0]
    assert count == 1  # no duplicate row

    audit_events = {r["event"] for r in conn.execute("SELECT event FROM audit_log").fetchall()}
    assert "partial_payment_updated" in audit_events
    conn.close()


def test_persist_exceptions_leaves_a_resolved_partial_payment_alone():
    from app.classify import persist_exceptions
    conn = _fresh_db()

    persist_exceptions(conn, [_partial_exception(["G1"], 100_000, "1 of ~3 pieces found")])
    conn.commit()
    conn.execute(
        "UPDATE exceptions SET status='resolved' WHERE exception_key='partial_payment:split:L1:-'"
    )
    conn.commit()

    persist_exceptions(conn, [_partial_exception(["G1", "G2"], 100_000, "2 of ~3 pieces found")])
    conn.commit()

    row = conn.execute(
        "SELECT status, detail FROM exceptions WHERE exception_key='partial_payment:split:L1:-'"
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["detail"] == "1 of ~3 pieces found"  # untouched
    conn.close()


# ---------------------------------------------------------------------------
# classify_and_persist_from_db(): a partial_payment auto-resolves once its
# group later graduates into a full split/batch match.
# ---------------------------------------------------------------------------

def test_partial_payment_auto_resolves_once_the_group_completes():
    from app.classify import classify_and_persist_from_db
    from app.load_fixtures import load_transactions

    conn = _fresh_db()
    ledger = [_row("ledger", "L1", 100_000, "Vendor A", "2026-01-01T10:00:00+00:00", reference_id="L1")]
    load_transactions(conn, ledger)
    # Pass 1: the split pass needs 2+ correlated candidates to activate at
    # all (a single candidate is fee_mismatch/timing_lag territory) -- two
    # pieces present, still short of the full 100,000.
    load_transactions(conn, [
        _row("gateway", "G1", 30_000, "Vendor A", "2026-01-01T10:05:00+00:00", reference_id="L1"),
        _row("gateway", "G2", 20_000, "Vendor A", "2026-01-01T10:10:00+00:00", reference_id="L1"),
    ])
    conn.commit()
    classify_and_persist_from_db(conn, actual_source="gateway")

    exc = conn.execute(
        "SELECT status FROM exceptions WHERE exception_key='partial_payment:split:L1:-'"
    ).fetchone()
    assert exc is not None
    assert exc["status"] == "open"

    # Pass 2: the remaining piece arrives -- the group now fully sums.
    load_transactions(conn, [_row("gateway", "G3", 50_000, "Vendor A", "2026-01-01T10:15:00+00:00", reference_id="L1")])
    conn.commit()
    classify_and_persist_from_db(conn, actual_source="gateway")

    exc = conn.execute(
        "SELECT status FROM exceptions WHERE exception_key='partial_payment:split:L1:-'"
    ).fetchone()
    assert exc["status"] == "resolved"

    audit_events = {r["event"] for r in conn.execute("SELECT event FROM audit_log").fetchall()}
    assert "partial_payment_completed" in audit_events
    conn.close()


# ---------------------------------------------------------------------------
# funnel.py: a batch-kind partial_payment stores a comma-joined list of
# ledger refs in one column -- COUNT(DISTINCT ledger_ref) would undercount
# it as one row instead of the N ledger rows it actually represents, which
# would silently break the funnel's matched+exceptions+recovered==ingested
# invariant. This locks in that it doesn't.
# ---------------------------------------------------------------------------

def test_funnel_invariant_holds_with_a_batch_kind_partial_payment():
    from app.classify import classify_and_persist_from_db
    from app.funnel import compute_funnel
    from app.load_fixtures import load_transactions

    conn = _fresh_db()
    ledger = [
        _row("ledger", "L1", 60_000, "Vendor A", "2026-01-01T10:00:00+00:00",
             reference_id="L1", settlement_batch_id="BATCH-1"),
        _row("ledger", "L2", 40_000, "Vendor A", "2026-01-01T10:00:00+00:00",
             reference_id="L2", settlement_batch_id="BATCH-1"),
    ]
    # The batch settlement only covers L1's share so far.
    gateway = [_row("gateway", "G1", 60_000, "Vendor A", "2026-01-01T10:15:00+00:00",
                     reference_id="BATCH-1")]
    load_transactions(conn, ledger)
    load_transactions(conn, gateway)
    conn.commit()

    classify_and_persist_from_db(conn, actual_source="gateway")

    # Pin down the mechanism, not just the arithmetic: this must be ONE
    # partial_payment exception spanning both ledger rows, not two
    # independent failed_payment rows that would coincidentally satisfy
    # the same invariant for the wrong reason.
    exc_rows = conn.execute("SELECT cause, ledger_ref FROM exceptions").fetchall()
    assert len(exc_rows) == 1
    assert exc_rows[0]["cause"] == "partial_payment"
    assert exc_rows[0]["ledger_ref"] == "L1,L2"

    funnel = compute_funnel(conn)
    assert funnel["ingested"] == 2
    # Both L1 and L2 are tied up in the one open partial_payment exception
    # -- neither should count as "matched" yet.
    assert funnel["exceptions"] == 2
    assert funnel["matched"] == 0
    assert funnel["matched"] + funnel["exceptions"] + funnel["recovered"] == funnel["ingested"]
    conn.close()


# ---------------------------------------------------------------------------
# Synthetic fixture: end-to-end demonstration of split/batch/partial cases,
# reconciled and classified through the real pipeline (not hand-built dicts).
# ---------------------------------------------------------------------------

def test_split_and_batch_fixture_reconciles_and_classifies_as_expected():
    from app.classify import classify
    from app.fixtures import generate_split_and_batch_cases
    from app.reconcile import reconcile

    ledger_rows, gateway_rows, ground_truth = generate_split_and_batch_cases()
    result = reconcile(ledger_rows, gateway_rows)

    assert len(result.split_matches) == 1
    assert len(result.batch_matches) == 1
    assert len(result.partial_groups) == 2  # one split-partial, one batch-partial

    exceptions = classify(result)
    partials = {e.exception_key: e for e in exceptions if e.cause == "partial_payment"}
    assert len(partials) == 2

    split_kind = [k for k in partials if k.startswith("partial_payment:split:")]
    batch_kind = [k for k in partials if k.startswith("partial_payment:batch:")]
    assert len(split_kind) == 1
    assert len(batch_kind) == 1


def test_reconcile_from_db_audit_log_mentions_split_and_batch_matches():
    """split_matches/batch_matches produce no exceptions -- they'd be
    silent otherwise. The audit trail is where they need to show up."""
    from app.fixtures import generate_split_and_batch_cases
    from app.load_fixtures import load_dataset_into_db
    from app.reconcile import reconcile_from_db

    ledger, gateway, _gt = generate_split_and_batch_cases()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger, gateway)

    reconcile_from_db(conn, actual_source="gateway")

    audit = conn.execute(
        "SELECT detail FROM audit_log WHERE event='reconciliation_complete'"
    ).fetchone()
    assert "split=1" in audit["detail"]
    assert "batch=1" in audit["detail"]
    conn.close()


def test_split_and_batch_fixture_loads_cleanly_and_is_deterministic():
    from app.fixtures import generate_split_and_batch_cases
    from app.load_fixtures import load_dataset_into_db

    l1, g1, gt1 = generate_split_and_batch_cases()
    l2, g2, gt2 = generate_split_and_batch_cases()
    assert l1 == l2 and g1 == g2 and gt1 == gt2

    conn = _fresh_db()
    load_dataset_into_db(conn, l1, g1)
    ledger_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE source='ledger'").fetchone()[0]
    gateway_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE source='gateway'").fetchone()[0]
    assert ledger_count == len(l1)
    assert gateway_count == len(g1)
    conn.close()


def test_a_bank_statement_pass_does_not_graduate_gateway_partial_payments():
    """Graduation is inferred from ABSENCE from the current pass's output, so
    it must only ever apply to rows that pass could have produced.

    Split/batch matching is gateway-side, so a 'bank_statement' pass emits no
    partial_payment rows at all. Unscoped, that made every open gateway-side
    partial payment look completed: reconciling a bank statement silently
    marked money 'resolved' whose own detail still read "short of the
    expected". Real money, wrongly called reconciled.
    """
    from app.classify import classify_and_persist_from_db
    from app.fixtures import (
        generate_bank_statement,
        generate_dataset,
        generate_settled_bank_lines,
        generate_split_and_batch_cases,
    )
    from app.load_fixtures import load_transactions

    conn = _fresh_db()
    ledger_rows, gateway_rows, _ = generate_dataset()
    split_ledger, split_gateway, _ = generate_split_and_batch_cases()
    bank_rows, _ = generate_bank_statement(ledger_rows)
    bank_rows += generate_settled_bank_lines(split_ledger)
    for rows in (ledger_rows, gateway_rows, split_ledger, split_gateway, bank_rows):
        load_transactions(conn, rows)
    conn.commit()

    classify_and_persist_from_db(conn, "gateway")
    before = {r["exception_key"]: r["status"] for r in conn.execute(
        "SELECT exception_key, status FROM exceptions WHERE cause='partial_payment'")}
    assert before, "fixture should produce at least one still-short partial payment"
    assert set(before.values()) == {"open"}

    classify_and_persist_from_db(conn, "bank_statement")
    after = {r["exception_key"]: r["status"] for r in conn.execute(
        "SELECT exception_key, status FROM exceptions WHERE cause='partial_payment'")}
    assert after == before, "a bank pass must not touch gateway-side partial payments"
