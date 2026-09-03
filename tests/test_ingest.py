"""Parsing and validating a user's own ledger / bank statement CSV.

The rule these tests exist to pin down is all-or-nothing: a file with any
bad row writes nothing and reports *every* problem at once, because the
alternative -- fix one row, re-upload, discover the next one -- is the
worst possible loop to put someone in with a 500-row export.

The second rule is that an uploaded row has to be comparable with a row
the fixture generator made. app/reconcile.py subtracts two occurred_at
values directly, and Python raises on naive-minus-aware, so a normalized
timestamp isn't cosmetic here -- see test_naive_occurred_at_is_normalized.
"""
import csv
import io

import pytest

from app.ingest import (
    SOURCES,
    IngestValidationError,
    build_template,
    parse_csv,
)

LEDGER_CSV = (
    "external_ref,amount_paise,occurred_at,counterparty,narration\n"
    "ORD-1,150000,2026-09-01T10:00:00+00:00,Acme Traders,Payout to Acme\n"
    "ORD-2,42050,2026-09-01T11:30:00+00:00,Bharat Logistics,Payout to Bharat\n"
)

BANK_CSV = (
    "external_ref,amount_paise,occurred_at,counterparty,narration,reference_id,"
    "transaction_type,original_ref\n"
    "TXN-1,150000,2026-09-02T10:00:00+00:00,Acme Traders,NEFT DR Acme,ORD-1,payment,\n"
    "TXN-2,42050,2026-09-02T11:30:00+00:00,Bharat Logistics,Reversal,,refund,TXN-1\n"
)


def _errors(source, text):
    with pytest.raises(IngestValidationError) as excinfo:
        parse_csv(source, text)
    return excinfo.value.errors


def _swap(text, old, new):
    assert old in text
    return text.replace(old, new)


def test_sources_are_ledger_and_bank_statement_only():
    # 'gateway' is RazorpayX's own record -- never a file a user holds.
    assert SOURCES == ("ledger", "bank_statement")


def test_parses_a_well_formed_ledger_file():
    rows = parse_csv("ledger", LEDGER_CSV)
    assert [r["external_ref"] for r in rows] == ["ORD-1", "ORD-2"]
    assert [r["amount_paise"] for r in rows] == [150000, 42050]
    assert rows[0]["counterparty"] == "Acme Traders"
    assert rows[0]["narration"] == "Payout to Acme"
    assert all(r["source"] == "ledger" for r in rows)


def test_ledger_rows_are_always_payments_correlated_by_their_own_ref():
    # The ledger is the expected side: every row is a payment, and its
    # reference_id is its own id (matching generate_dataset()).
    rows = parse_csv("ledger", LEDGER_CSV)
    assert [r["reference_id"] for r in rows] == ["ORD-1", "ORD-2"]
    assert all(r["transaction_type"] == "payment" for r in rows)
    assert all(r["original_ref"] is None for r in rows)


def test_parses_a_well_formed_bank_statement_file():
    rows = parse_csv("bank_statement", BANK_CSV)
    assert [r["external_ref"] for r in rows] == ["TXN-1", "TXN-2"]
    assert rows[0]["reference_id"] == "ORD-1"
    assert rows[0]["transaction_type"] == "payment"
    assert rows[1]["transaction_type"] == "refund"
    assert rows[1]["original_ref"] == "TXN-1"
    assert all(r["source"] == "bank_statement" for r in rows)


def test_blank_optional_cells_become_none_not_empty_strings():
    # A NULL reference_id is what forces the matcher's fuzzy fallback;
    # an empty string would be a value, and would match nothing.
    rows = parse_csv("bank_statement", BANK_CSV)
    assert rows[1]["reference_id"] is None
    assert rows[0]["original_ref"] is None


def test_bank_transaction_type_defaults_to_payment_when_column_absent():
    text = (
        "external_ref,amount_paise,occurred_at\n"
        "TXN-1,150000,2026-09-02T10:00:00+00:00\n"
    )
    rows = parse_csv("bank_statement", text)
    assert rows[0]["transaction_type"] == "payment"
    assert rows[0]["original_ref"] is None


def test_date_only_occurred_at_is_midnight_utc_that_day():
    text = _swap(LEDGER_CSV, "2026-09-01T10:00:00+00:00", "2026-09-01")
    rows = parse_csv("ledger", text)
    assert rows[0]["occurred_at"] == "2026-09-01T00:00:00+00:00"


def test_naive_occurred_at_is_normalized_to_utc():
    # app/reconcile.py does `t1 - t2` on parsed occurred_at values. A naive
    # uploaded row minus an aware fixture row raises TypeError, so leaving
    # the offset off would break reconciliation, not just formatting.
    text = _swap(LEDGER_CSV, "2026-09-01T10:00:00+00:00", "2026-09-01 10:00:00")
    rows = parse_csv("ledger", text)
    assert rows[0]["occurred_at"] == "2026-09-01T10:00:00+00:00"


def test_a_trailing_z_timestamp_is_accepted():
    # Real bank exports write UTC as 'Z'; datetime.fromisoformat only
    # learned to read that in 3.11, and this app runs on 3.9.
    text = _swap(LEDGER_CSV, "2026-09-01T10:00:00+00:00", "2026-09-01T10:00:00Z")
    rows = parse_csv("ledger", text)
    assert rows[0]["occurred_at"] == "2026-09-01T10:00:00+00:00"


def test_surrounding_whitespace_is_stripped():
    text = _swap(LEDGER_CSV, "ORD-1,150000", "  ORD-1  ,  150000  ")
    rows = parse_csv("ledger", text)
    assert rows[0]["external_ref"] == "ORD-1"
    assert rows[0]["amount_paise"] == 150000


def test_unknown_columns_are_ignored():
    # Someone's export will have extra columns. Dropping them is friendlier
    # than refusing the file over a column we simply have no use for.
    text = _swap(
        LEDGER_CSV,
        "external_ref,amount_paise",
        "gl_code,external_ref,amount_paise",
    ).replace("\nORD-1", "\n4001,ORD-1").replace("\nORD-2", "\n4002,ORD-2")
    rows = parse_csv("ledger", text)
    assert rows[0]["external_ref"] == "ORD-1"
    assert "gl_code" not in rows[0]


def test_missing_required_column_is_reported_against_the_header():
    text = "external_ref,occurred_at\nORD-1,2026-09-01T10:00:00+00:00\n"
    assert _errors("ledger", text) == [
        {"row_number": 0, "column": "amount_paise",
         "message": "required column is missing from the header"}
    ]


def test_a_file_with_no_data_rows_is_rejected():
    # Uploading replaces everything for that source, so an empty file must
    # not read as "delete it all" -- that is a mistake, not an instruction.
    errors = _errors("ledger", "external_ref,amount_paise,occurred_at\n")
    assert errors == [
        {"row_number": 0, "column": "", "message": "file has no data rows"}
    ]


def test_an_empty_file_is_rejected():
    errors = _errors("ledger", "")
    assert errors == [
        {"row_number": 0, "column": "", "message": "file has no header row"}
    ]


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError):
        parse_csv("gateway", LEDGER_CSV)


def test_non_numeric_amount_is_rejected():
    # Rupees-with-decimals is the mistake this actually catches in practice.
    text = _swap(LEDGER_CSV, "ORD-1,150000", "ORD-1,1500.00")
    assert _errors("ledger", text) == [
        {"row_number": 1, "column": "amount_paise",
         "message": "must be a whole number of paise, got '1500.00'"}
    ]


def test_a_missing_required_value_is_rejected():
    text = _swap(LEDGER_CSV, "ORD-2,42050", "ORD-2,")
    assert _errors("ledger", text) == [
        {"row_number": 2, "column": "amount_paise", "message": "required value is empty"}
    ]


@pytest.mark.parametrize("amount", ["0", "-500"])
def test_a_non_positive_amount_is_rejected(amount):
    text = _swap(LEDGER_CSV, "ORD-1,150000", f"ORD-1,{amount}")
    assert _errors("ledger", text) == [
        {"row_number": 1, "column": "amount_paise",
         "message": f"must be greater than zero, got {amount}"}
    ]


def test_unparseable_occurred_at_is_rejected():
    text = _swap(LEDGER_CSV, "2026-09-01T10:00:00+00:00", "01/09/2026")
    assert _errors("ledger", text) == [
        {"row_number": 1, "column": "occurred_at",
         "message": "must be an ISO 8601 date or datetime, got '01/09/2026'"}
    ]


def test_a_reversal_without_an_original_ref_is_rejected():
    text = _swap(BANK_CSV, "Reversal,,refund,TXN-1", "Reversal,,refund,")
    assert _errors("bank_statement", text) == [
        {"row_number": 2, "column": "original_ref",
         "message": "required for a refund row, to trace what it reverses"}
    ]


def test_an_unknown_transaction_type_is_rejected():
    text = _swap(BANK_CSV, ",refund,TXN-1", ",reversal,TXN-1")
    assert _errors("bank_statement", text) == [
        {"row_number": 2, "column": "transaction_type",
         "message": "must be one of payment, refund, chargeback, got 'reversal'"}
    ]


def test_a_duplicate_external_ref_within_one_file_is_rejected():
    text = _swap(LEDGER_CSV, "ORD-2,42050", "ORD-1,42050")
    assert _errors("ledger", text) == [
        {"row_number": 2, "column": "external_ref",
         "message": "duplicate of row 1 in this file"}
    ]


def test_every_bad_row_is_reported_at_once():
    # The all-or-nothing promise: one upload, one complete list of problems,
    # in file order.
    text = (
        "external_ref,amount_paise,occurred_at\n"
        "ORD-1,oops,2026-09-01T10:00:00+00:00\n"
        "ORD-2,150000,not-a-date\n"
        "ORD-3,150000,2026-09-01T10:00:00+00:00\n"
        "ORD-1,150000,2026-09-01T10:00:00+00:00\n"
    )
    errors = _errors("ledger", text)
    assert [(e["row_number"], e["column"]) for e in errors] == [
        (1, "amount_paise"), (2, "occurred_at"), (4, "external_ref")
    ]


@pytest.mark.parametrize("source", SOURCES)
def test_template_round_trips_through_the_parser(source):
    # The template and the parser read the same schema table, and this is
    # what proves it: whatever the template hands a user parses back clean.
    template = build_template(source)
    rows = parse_csv(source, template)
    assert len(rows) == 1
    assert rows[0]["source"] == source

    header = next(csv.reader(io.StringIO(template)))
    appended = template + ",".join(
        {"external_ref": "ORD-99", "amount_paise": "9900",
         "occurred_at": "2026-09-01T10:00:00+00:00"}.get(c, "")
        for c in header
    ) + "\n"
    rows = parse_csv(source, appended)
    assert len(rows) == 2
    assert rows[1]["external_ref"] == "ORD-99"
    assert rows[1]["amount_paise"] == 9900


def test_the_bank_statement_template_carries_no_payout_dispatch_columns():
    """A bank statement row is an OBSERVATION of a payout that already
    happened -- it is never an instruction, so it has no business asking for
    a payee's bank details.

    This originally applied to the ledger template too: uploaded rows were
    reconcilable and deliberately not dispatchable. That decision was
    reversed on purpose -- see tests/test_ingest_payout.py -- because a
    ledger row IS an instruction, and refusing the columns meant an uploaded
    backlog could be detected but never actually recovered. The bank side of
    the rule stands unchanged.
    """
    header = next(csv.reader(io.StringIO(build_template("bank_statement"))))
    assert not [c for c in header if c.startswith(("fund_account", "contact_", "payout_"))]
