import sqlite3
from pathlib import Path

from app.fixtures import (
    BANK_CASE_PLAN,
    BANK_EXTRA_PLAN,
    CASE_PLAN,
    generate_bank_statement,
    generate_dataset,
    payout_fields,
    vendor_profile,
)
from app.load_fixtures import load_dataset_into_db, load_transactions

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
    # every case type produces one case/ground-truth entry, but "unexplained"
    # produces no ledger row (it's a gateway-only orphan -- see fixtures.py)
    expected_case_count = sum(count for _, count in CASE_PLAN)
    expected_ledger_count = sum(count for case_type, count in CASE_PLAN
                                 if case_type != "unexplained")
    assert len(ledger_rows) == expected_ledger_count
    assert len(ground_truth["cases"]) == expected_case_count
    # every case type appears exactly as many times as planned
    seen = {}
    for case in ground_truth["cases"]:
        seen[case["case_type"]] = seen.get(case["case_type"], 0) + 1
    assert seen == dict(CASE_PLAN)


def test_gateway_row_count_matches_case_shapes():
    ledger_rows, gateway_rows, ground_truth = generate_dataset()
    # exact_match/fee_mismatch/timing_lag/unexplained -> 1 gateway row each,
    # duplicate -> 2, failed_payment -> 0
    expected = {
        "exact_match": 1, "fee_mismatch": 1, "duplicate": 2,
        "timing_lag": 1, "failed_payment": 0, "unexplained": 1,
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


# ---------------------------------------------------------------------------
# Bank statement fixture -- the third source, reconciled against the same
# ledger rows generate_dataset() already produced.
# ---------------------------------------------------------------------------

def test_bank_statement_generation_is_deterministic():
    ledger_rows, _gateway_rows, _gt = generate_dataset()
    bank1, bgt1 = generate_bank_statement(ledger_rows)
    bank2, bgt2 = generate_bank_statement(ledger_rows)
    assert bank1 == bank2
    assert bgt1 == bgt2


def test_bank_case_plan_covers_every_ledger_row_exactly_once():
    ledger_rows, _gateway_rows, _gt = generate_dataset()
    bank_rows, ground_truth = generate_bank_statement(ledger_rows)

    per_ledger_cases = [c for c in ground_truth["cases"] if c["ledger_ref"] is not None
                        and c["case_type"] in dict(BANK_CASE_PLAN)]
    assert len(per_ledger_cases) == len(ledger_rows)
    assert {c["ledger_ref"] for c in per_ledger_cases} == {r["external_ref"] for r in ledger_rows}

    seen = {}
    for case in ground_truth["cases"]:
        seen[case["case_type"]] = seen.get(case["case_type"], 0) + 1
    assert seen == {**dict(BANK_CASE_PLAN), **dict(BANK_EXTRA_PLAN)}


def test_bank_row_count_matches_case_shapes():
    ledger_rows, _gateway_rows, _gt = generate_dataset()
    bank_rows, ground_truth = generate_bank_statement(ledger_rows)
    # every case type produces exactly one bank row except failed_payment
    # (0 -- the bank simply hasn't reflected that debit yet)
    expected_total = sum(
        0 if case["case_type"] == "failed_payment" else 1
        for case in ground_truth["cases"]
    )
    assert len(bank_rows) == expected_total
    for case in ground_truth["cases"]:
        expected_refs = 0 if case["case_type"] == "failed_payment" else 1
        assert len(case["bank_refs"]) == expected_refs


def test_refund_and_chargeback_rows_carry_transaction_type_and_original_ref():
    ledger_rows, _gateway_rows, _gt = generate_dataset()
    bank_rows, ground_truth = generate_bank_statement(ledger_rows)
    ledger_refs = {r["external_ref"] for r in ledger_rows}
    bank_by_ref = {r["external_ref"]: r for r in bank_rows}

    for case in ground_truth["cases"]:
        if case["case_type"] not in ("refund_unmatched", "chargeback"):
            continue
        row = bank_by_ref[case["bank_refs"][0]]
        assert row["transaction_type"] == ("refund" if case["case_type"] == "refund_unmatched" else "chargeback")
        assert row["original_ref"] == case["ledger_ref"]
        assert row["original_ref"] in ledger_refs  # reverses a real, already-cleared payout


def test_bank_fee_mismatch_rows_actually_differ_in_amount():
    ledger_rows, _gateway_rows, _gt = generate_dataset()
    bank_rows, ground_truth = generate_bank_statement(ledger_rows)
    ledger_by_ref = {r["external_ref"]: r for r in ledger_rows}
    bank_by_ref = {r["external_ref"]: r for r in bank_rows}

    for case in ground_truth["cases"]:
        if case["case_type"] != "fee_mismatch":
            continue
        ledger_amount = ledger_by_ref[case["ledger_ref"]]["amount_paise"]
        bank_amount = bank_by_ref[case["bank_refs"][0]]["amount_paise"]
        assert bank_amount == ledger_amount - case["bank_charge_paise"]


def test_bank_statement_rows_load_cleanly_alongside_ledger_and_gateway():
    ledger_rows, gateway_rows, _gt = generate_dataset()
    bank_rows, bank_gt = generate_bank_statement(ledger_rows)

    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    load_transactions(conn, bank_rows)
    conn.commit()

    bank_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source='bank_statement'"
    ).fetchone()[0]
    assert bank_count == bank_gt["totals"]["bank_rows"]

    refund_row = conn.execute(
        "SELECT transaction_type, original_ref FROM transactions "
        "WHERE source='bank_statement' AND transaction_type='refund' LIMIT 1"
    ).fetchone()
    assert refund_row is not None
    assert refund_row["original_ref"] is not None
    conn.close()


# ---------------------------------------------------------------------------
# RazorpayX Tally batch-payout fields on ledger rows.
# ---------------------------------------------------------------------------

PAYOUT_FIELDS = (
    "payout_purpose", "payout_mode", "fund_account_type", "fund_account_name",
    "contact_type", "contact_email", "contact_mobile", "contact_address",
    "contact_city", "contact_zipcode", "contact_state", "notes",
)


def test_every_ledger_row_carries_the_payout_instruction():
    ledger_rows, _, _ = generate_dataset()
    for row in ledger_rows:
        for field in PAYOUT_FIELDS:
            assert row.get(field), f"{row['external_ref']} is missing {field}"


def test_only_ledger_rows_carry_payout_fields():
    # A gateway/bank_statement row is an observation of a payout that already
    # happened, not an instruction to make one -- it has no fund account.
    ledger_rows, gateway_rows, _ = generate_dataset()
    bank_rows, _ = generate_bank_statement(ledger_rows)
    for row in gateway_rows + bank_rows:
        for field in PAYOUT_FIELDS:
            assert field not in row, f"{row['external_ref']} should not carry {field}"


def test_fund_account_type_and_its_details_never_contradict():
    ledger_rows, _, _ = generate_dataset()
    seen = set()
    for row in ledger_rows:
        seen.add(row["fund_account_type"])
        if row["fund_account_type"] == "bank_account":
            assert row["fund_account_ifsc"]
            assert row["fund_account_number"]
            assert row["fund_account_vpa"] is None
            # RTGS has a 2,00,000 rupee floor no fixture amount reaches.
            assert row["payout_mode"] in ("NEFT", "IMPS")
        else:
            assert row["fund_account_type"] == "vpa"
            assert row["fund_account_vpa"]
            assert row["fund_account_ifsc"] is None
            assert row["fund_account_number"] is None
            # A VPA fund account is only reachable over UPI.
            assert row["payout_mode"] == "UPI"
    # both branches of the template are actually exercised by the dataset
    assert seen == {"bank_account", "vpa"}


def test_ifsc_follows_the_real_layout():
    # Four-letter bank code, a literal '0', then six characters.
    ledger_rows, _, _ = generate_dataset()
    ifscs = [r["fund_account_ifsc"] for r in ledger_rows if r["fund_account_ifsc"]]
    assert ifscs
    for ifsc in ifscs:
        assert len(ifsc) == 11
        assert ifsc[:4].isalpha() and ifsc[:4].isupper()
        assert ifsc[4] == "0"


def test_one_vendor_always_gets_the_same_bank_details():
    # Profiles key off the vendor name, so the same vendor reconciled in two
    # different rows must never appear to bank somewhere else.
    ledger_rows, _, _ = generate_dataset()
    by_vendor = {}
    for row in ledger_rows:
        prior = by_vendor.setdefault(row["counterparty"], row)
        for field in ("fund_account_type", "fund_account_ifsc",
                      "fund_account_number", "fund_account_vpa",
                      "contact_email", "contact_city", "contact_zipcode"):
            assert row[field] == prior[field]


def test_vendor_profiles_are_stable_across_processes():
    # Seeded from a SHA-256 of the name rather than hash(), which is salted
    # per interpreter run -- this is the assertion that would catch a
    # regression back to hash().
    import subprocess
    import sys

    code = (
        "from app.fixtures import vendor_profile; "
        "print(vendor_profile('Acme Traders')['fund_account_ifsc'])"
    )
    runs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, check=True).stdout.strip()
        for _ in range(2)
    }
    assert len(runs) == 1
    assert runs == {vendor_profile("Acme Traders")["fund_account_ifsc"]}


def test_payout_purpose_follows_transaction_type():
    assert payout_fields("Acme Traders", "LED-0001")["payout_purpose"] == "vendor bill"
    assert payout_fields("Acme Traders", "LED-0001", "refund")["payout_purpose"] == "refund"


def test_payout_fields_survive_the_round_trip_into_the_db():
    conn = _fresh_db()
    ledger_rows, gateway_rows, _ = generate_dataset()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)

    stored = conn.execute(
        """SELECT * FROM transactions
           WHERE source='ledger' AND external_ref=?""",
        (ledger_rows[0]["external_ref"],),
    ).fetchone()
    for field in PAYOUT_FIELDS:
        assert stored[field] == ledger_rows[0][field]

    # and the constrained columns stay NULL on the non-ledger side
    gw_nulls = conn.execute(
        """SELECT COUNT(*) FROM transactions
           WHERE source != 'ledger'
             AND (payout_purpose IS NOT NULL OR payout_mode IS NOT NULL
                  OR fund_account_type IS NOT NULL)"""
    ).fetchone()[0]
    assert gw_nulls == 0


# ---------------------------------------------------------------------------
# Settled bank lines for ledger rows generate_bank_statement() doesn't cover.
# ---------------------------------------------------------------------------

def test_settled_bank_lines_are_deterministic_and_exactly_match_their_ledger_row():
    from app.fixtures import generate_settled_bank_lines, generate_split_and_batch_cases

    split_ledger, _, _ = generate_split_and_batch_cases()
    first = generate_settled_bank_lines(split_ledger)
    assert first == generate_settled_bank_lines(split_ledger)
    assert len(first) == len(split_ledger)

    by_ref = {r["reference_id"]: r for r in first}
    for led in split_ledger:
        line = by_ref[led["external_ref"]]
        assert line["source"] == "bank_statement"
        # same money, and landing after the ledger says it left
        assert line["amount_paise"] == led["amount_paise"]
        assert line["occurred_at"] > led["occurred_at"]


def test_split_and_batch_rows_raise_no_bank_side_exceptions():
    """The regression this generator exists to prevent: without settled bank
    lines, reconciling the split/batch ledger rows against 'bank_statement'
    reports six failed_payments -- the ledger says the money left and the bank
    has no record of it. That reads as a real finding but is only the fixture
    being incomplete on the bank side."""
    from app.classify import classify_and_persist_from_db
    from app.fixtures import (
        generate_bank_statement,
        generate_settled_bank_lines,
        generate_split_and_batch_cases,
    )

    conn = _fresh_db()
    ledger_rows, gateway_rows, _ = generate_dataset()
    split_ledger, split_gateway, _ = generate_split_and_batch_cases()
    bank_rows, _ = generate_bank_statement(ledger_rows)
    bank_rows += generate_settled_bank_lines(split_ledger)

    for rows in (ledger_rows, gateway_rows, split_ledger, split_gateway, bank_rows):
        load_transactions(conn, rows)
    conn.commit()

    classify_and_persist_from_db(conn, "gateway")
    classify_and_persist_from_db(conn, "bank_statement")

    assert conn.execute(
        """SELECT COUNT(*) FROM exceptions WHERE matched_source='bank_statement'
             AND (ledger_ref LIKE 'SPLIT%' OR ledger_ref LIKE 'BATCH%')"""
    ).fetchone()[0] == 0

    # and the bank pass reports exactly the failures its own plan describes
    planned = dict(BANK_CASE_PLAN)["failed_payment"]
    assert conn.execute(
        "SELECT COUNT(*) FROM exceptions WHERE matched_source='bank_statement' AND cause='failed_payment'"
    ).fetchone()[0] == planned


def test_the_demo_dataset_exercises_every_cause():
    from app.classify import CAUSES, classify_and_persist_from_db
    from app.fixtures import (
        generate_bank_statement,
        generate_settled_bank_lines,
        generate_split_and_batch_cases,
    )

    conn = _fresh_db()
    ledger_rows, gateway_rows, _ = generate_dataset()
    split_ledger, split_gateway, _ = generate_split_and_batch_cases()
    bank_rows, _ = generate_bank_statement(ledger_rows)
    bank_rows += generate_settled_bank_lines(split_ledger)
    for rows in (ledger_rows, gateway_rows, split_ledger, split_gateway, bank_rows):
        load_transactions(conn, rows)
    conn.commit()

    classify_and_persist_from_db(conn, "gateway")
    classify_and_persist_from_db(conn, "bank_statement")

    seen = {r[0] for r in conn.execute("SELECT DISTINCT cause FROM exceptions")}
    assert seen == set(CAUSES), f"causes never exercised: {sorted(set(CAUSES) - seen)}"
