"""Payout-instruction columns on the uploaded ledger.

These ten columns are what turn an uploaded row from *reconcilable* into
*dispatchable*. They are all optional -- a file without any of them behaves
exactly as it did before -- but the moment fund_account_type appears the row
has to be complete, and that is what these tests pin down.

Incompleteness has to fail at PARSE time, not at dispatch time. schema.sql
makes bank_account and vpa mutually exclusive shapes, and RazorpayX rejects a
body carrying the wrong one outright; a half-filled row that loads cleanly and
then fails days later during a routing pass is the worst possible ordering,
because by then it is one silent failure among fifty.
"""
import csv
import io

import pytest

from app.ingest import IngestValidationError, build_template, parse_csv

BASE = "external_ref,amount_paise,occurred_at,counterparty,narration"
ROW = "LED-1,150000,2026-09-01T10:00:00+00:00,Acme Traders,Payout"

BANK_COLS = (",fund_account_type,fund_account_name,fund_account_ifsc,"
             "fund_account_number,fund_account_vpa,contact_type,"
             "payout_purpose,payout_mode")


def _csv(extra_header: str, extra_row: str) -> str:
    return f"{BASE}{extra_header}\n{ROW}{extra_row}\n"


def _errors(text, source="ledger"):
    with pytest.raises(IngestValidationError) as excinfo:
        parse_csv(source, text)
    return excinfo.value.errors


def _messages(text):
    return {(e["column"], e["message"]) for e in _errors(text)}


# --- backwards compatibility -----------------------------------------------

def test_a_file_with_no_payout_columns_still_parses():
    # The whole point of "optional": every ledger CSV written before this
    # feature existed must keep working, unchanged.
    rows = parse_csv("ledger", _csv("", ""))
    assert rows[0]["external_ref"] == "LED-1"
    assert rows[0].get("fund_account_type") is None


def test_the_bank_statement_template_gains_no_payout_columns():
    # Only a ledger row is an instruction. A bank statement row is an
    # observation of a payout that already happened.
    header = next(csv.reader(io.StringIO(build_template("bank_statement"))))
    assert not [c for c in header if c.startswith(("fund_account", "contact_", "payout_"))]


def test_the_ledger_template_offers_the_payout_columns():
    header = next(csv.reader(io.StringIO(build_template("ledger"))))
    for column in ("fund_account_type", "fund_account_ifsc", "fund_account_number",
                   "fund_account_vpa", "contact_type", "payout_purpose", "payout_mode"):
        assert column in header


# --- the two valid shapes --------------------------------------------------

def test_a_complete_bank_account_row_parses():
    rows = parse_csv("ledger", _csv(
        BANK_COLS,
        ",bank_account,Acme Traders,HDFC0001234,50100123456789,,vendor,vendor bill,NEFT"))
    row = rows[0]
    assert row["fund_account_type"] == "bank_account"
    assert row["fund_account_ifsc"] == "HDFC0001234"
    assert row["fund_account_number"] == "50100123456789"
    assert row["fund_account_vpa"] is None
    assert row["contact_type"] == "vendor"
    assert row["payout_purpose"] == "vendor bill"
    assert row["payout_mode"] == "NEFT"


def test_a_complete_vpa_row_parses():
    rows = parse_csv("ledger", _csv(
        BANK_COLS, ",vpa,Acme Traders,,,acme@okhdfcbank,vendor,vendor bill,UPI"))
    row = rows[0]
    assert row["fund_account_type"] == "vpa"
    assert row["fund_account_vpa"] == "acme@okhdfcbank"
    assert row["fund_account_ifsc"] is None
    assert row["fund_account_number"] is None


# --- incomplete bank_account ------------------------------------------------

def test_a_bank_account_row_without_an_ifsc_is_rejected():
    assert ("fund_account_ifsc",
            "required when fund_account_type is 'bank_account'") in _messages(
        _csv(BANK_COLS, ",bank_account,Acme,,50100123456789,,vendor,vendor bill,NEFT"))


def test_a_bank_account_row_without_an_account_number_is_rejected():
    assert ("fund_account_number",
            "required when fund_account_type is 'bank_account'") in _messages(
        _csv(BANK_COLS, ",bank_account,Acme,HDFC0001234,,,vendor,vendor bill,NEFT"))


def test_a_bank_account_row_carrying_a_vpa_is_rejected():
    # schema.sql: "'bank_account' rows carry ifsc + number and no vpa".
    # RazorpayX rejects a body with the wrong shape for its account_type.
    assert ("fund_account_vpa",
            "must be empty when fund_account_type is 'bank_account'") in _messages(
        _csv(BANK_COLS,
             ",bank_account,Acme,HDFC0001234,50100123456789,acme@okhdfcbank,vendor,vendor bill,NEFT"))


# --- incomplete vpa ---------------------------------------------------------

def test_a_vpa_row_without_a_vpa_is_rejected():
    assert ("fund_account_vpa",
            "required when fund_account_type is 'vpa'") in _messages(
        _csv(BANK_COLS, ",vpa,Acme,,,,vendor,vendor bill,UPI"))


def test_a_vpa_row_carrying_bank_details_is_rejected():
    problems = _messages(
        _csv(BANK_COLS, ",vpa,Acme,HDFC0001234,50100123456789,acme@ok,vendor,vendor bill,UPI"))
    assert ("fund_account_ifsc", "must be empty when fund_account_type is 'vpa'") in problems
    assert ("fund_account_number", "must be empty when fund_account_type is 'vpa'") in problems


# --- the silent-undispatchable trap ----------------------------------------

def test_bank_details_without_a_fund_account_type_are_rejected():
    # The dangerous case: someone fills in an account number, the row loads
    # clean, and nothing dispatches -- with no error anywhere saying why.
    assert ("fund_account_type",
            "required when any fund_account or contact column is set") in _messages(
        _csv(BANK_COLS, ",,Acme,HDFC0001234,50100123456789,,vendor,vendor bill,NEFT"))


# --- vocabularies -----------------------------------------------------------

@pytest.mark.parametrize("column,value,allowed", [
    ("contact_type", "supplier", "vendor, customer, employee, self"),
    ("payout_purpose", "invoice", "refund, cashback, payout, salary, utility bill, vendor bill"),
    ("payout_mode", "SWIFT", "NEFT, RTGS, IMPS, UPI, card"),
])
def test_a_value_outside_the_schema_vocabulary_is_rejected(column, value, allowed):
    # These mirror schema.sql's CHECK constraints. Without them the row loads
    # and the INSERT fails, surfacing as a 500 rather than a fixable message.
    row = {"contact_type": "vendor", "payout_purpose": "vendor bill", "payout_mode": "NEFT"}
    row[column] = value
    text = _csv(BANK_COLS,
                f",bank_account,Acme,HDFC0001234,50100123456789,,"
                f"{row['contact_type']},{row['payout_purpose']},{row['payout_mode']}")
    assert (column, f"must be one of {allowed}, got {value!r}") in _messages(text)


def test_the_ledger_template_round_trips_with_its_payout_columns():
    # The generated template must itself be a valid, dispatchable row --
    # otherwise the example we hand people fails the rules we just added.
    template = build_template("ledger")
    rows = parse_csv("ledger", template)
    assert len(rows) == 1
    assert rows[0]["fund_account_type"] in ("bank_account", "vpa")
