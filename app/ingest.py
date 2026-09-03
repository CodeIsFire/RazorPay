"""Ingesting a user's own ledger / bank statement CSV.

This is the real-data counterpart to app/fixtures.py: same `transactions`
table, same downstream pipeline, but rows a user actually has instead of
rows we generated. `gateway` is deliberately absent -- that side is
RazorpayX's own record, sourced from the live API or webhooks, never a
file anyone holds.

Two rules shape everything below:

1. **All-or-nothing.** parse_csv() validates every row before the caller
   is allowed to write any of them, and reports every problem at once.
   The alternative -- reject on the first bad row -- turns a 500-row
   export into a fix-one-reupload loop, which is the worst experience
   this feature could have.
2. **One schema table.** COLUMNS below is read by both the parser and the
   template generator, so the file we hand a user and the file we accept
   back cannot drift apart. tests/test_ingest.py round-trips a generated
   template through the parser to keep that honest.

There is one restatement of these rules that this module cannot reach:
frontend/src/lib/conversionPrompt.ts, the prompt shown on the Data tab for
converting someone's own export. It hard-codes the column lists, the paise
requirement, the accepted date formats and the reversal rule. Change the
schema table or a validator below and that string needs the same edit, or
it will start producing files this parser rejects.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db import log_audit
from app.load_fixtures import load_transactions

SOURCES = ("ledger", "bank_statement")

# schema.sql's own vocabulary, repeated here so a bad value is a row-level
# validation error the user can act on rather than a CHECK constraint
# failure surfacing as a 500.
TRANSACTION_TYPES = ("payment", "refund", "chargeback")

# The rest of schema.sql's CHECK vocabularies, repeated for the same reason.
FUND_ACCOUNT_TYPES = ("bank_account", "vpa")
CONTACT_TYPES = ("vendor", "customer", "employee", "self")
PAYOUT_PURPOSES = ("refund", "cashback", "payout", "salary", "utility bill", "vendor bill")
PAYOUT_MODES = ("NEFT", "RTGS", "IMPS", "UPI", "card")

# 'bank_account' and 'vpa' are mutually exclusive shapes -- RazorpayX rejects a
# body carrying the wrong one for its account_type (see
# razorpayx_client.build_composite_fund_account), so a row carrying both, or
# missing half of one, can never be dispatched. Catching it at parse time keeps
# that failure attached to the row that caused it.
_REQUIRED_BY_FUND_TYPE = {
    "bank_account": ("fund_account_ifsc", "fund_account_number"),
    "vpa": ("fund_account_vpa",),
}
_FORBIDDEN_BY_FUND_TYPE = {
    "bank_account": ("fund_account_vpa",),
    "vpa": ("fund_account_ifsc", "fund_account_number"),
}
# Setting any of these without a fund_account_type produces a row that loads
# clean and silently never dispatches -- the worst outcome, so it is an error.
_IMPLIES_FUND_ACCOUNT = (
    "fund_account_name", "fund_account_ifsc", "fund_account_number",
    "fund_account_vpa", "contact_type", "contact_email", "contact_mobile",
)


@dataclass(frozen=True)
class Column:
    name: str
    required: bool = False
    # How the cell is validated and converted. 'text' is the default and
    # does neither beyond stripping.
    kind: str = "text"
    example: str = ""
    # For kind='choice': the values schema.sql's CHECK constraint permits.
    # Validating here turns a constraint violation (a 500 nobody can act on)
    # into a row-level message naming the column and the allowed values.
    allowed: tuple = ()


# Reconciliation fields only. The RazorpayX payout-instruction columns that
# schema.sql carries (fund account, contact, payout purpose/mode) are
# deliberately NOT offered here: an uploaded row is reconcilable, not
# dispatchable, and asking for a payee's bank details in a reconciliation
# template invites someone to paste real ones into a feature that will
# never use them.
_BASE_COLUMNS = (
    Column("external_ref", required=True, example="ORD-1001"),
    Column("amount_paise", required=True, kind="amount", example="150000"),
    Column("occurred_at", required=True, kind="timestamp",
           example="2026-09-01T10:00:00+00:00"),
    Column("counterparty", example="Acme Traders"),
    Column("narration", example="Payout to Acme Traders"),
)

# The payout instruction, mirroring schema.sql's Tally payout columns. All
# optional: a ledger CSV without any of them is reconcilable and nothing more,
# exactly as before this existed. Supplying them makes the row dispatchable --
# which is why _check_row_rules refuses a half-filled one rather than loading a
# row that can never actually pay anyone.
_PAYOUT_COLUMNS = (
    Column("fund_account_type", kind="choice", allowed=FUND_ACCOUNT_TYPES,
           example="bank_account"),
    Column("fund_account_name", example="Acme Traders"),
    Column("fund_account_ifsc", example="HDFC0001234"),
    Column("fund_account_number", example="50100123456789"),
    # Empty in the template on purpose: the example row is a bank_account,
    # and carrying both shapes at once is exactly what the rules forbid.
    Column("fund_account_vpa", example=""),
    Column("contact_type", kind="choice", allowed=CONTACT_TYPES, example="vendor"),
    Column("contact_email", example="ap@acmetraders.example"),
    Column("contact_mobile", example="9000090000"),
    Column("payout_purpose", kind="choice", allowed=PAYOUT_PURPOSES, example="vendor bill"),
    Column("payout_mode", kind="choice", allowed=PAYOUT_MODES, example="NEFT"),
)

_LEDGER_COLUMNS = _BASE_COLUMNS + _PAYOUT_COLUMNS

# The bank side gets three more. reference_id is optional on purpose: a
# statement row without one forces the matcher's fuzzy fallback, exactly
# like generate_bank_statement()'s rows do.
_BANK_COLUMNS = _BASE_COLUMNS + (
    Column("reference_id", example="ORD-1001"),
    Column("transaction_type", kind="choice", allowed=TRANSACTION_TYPES, example="payment"),
    Column("original_ref", example=""),
)

COLUMNS: dict[str, tuple[Column, ...]] = {
    "ledger": _LEDGER_COLUMNS,
    "bank_statement": _BANK_COLUMNS,
}


class IngestValidationError(Exception):
    """Every problem in one file, not the first one found.

    `errors` is a list of {row_number, column, message}. row_number is
    1-indexed over data rows -- the header is not row 1 -- so it lines up
    with what a spreadsheet shows minus its header gridline. 0 means the
    problem is with the file or its header rather than any one row.
    """

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) in the uploaded file")


def _error(row_number: int, column: str, message: str) -> dict:
    return {"row_number": row_number, "column": column, "message": message}


def _schema(source: str) -> tuple[Column, ...]:
    if source not in COLUMNS:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    return COLUMNS[source]


def _parse_timestamp(value: str) -> str:
    """ISO 8601 in, ISO 8601 UTC out.

    Normalizing is load-bearing, not cosmetic: app/reconcile.py subtracts
    two occurred_at values directly, and Python raises TypeError on
    naive-minus-aware. A naive uploaded row reconciled against a
    tz-aware fixture row would crash the matcher, so a missing offset is
    read as UTC here rather than carried through.
    """
    # 'Z' is what real bank exports write, and datetime.fromisoformat only
    # learned to read it in 3.11.
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_csv(source: str, text: str, max_rows: int | None = None) -> list[dict]:
    """CSV text -> rows ready for load_transactions(), or raise with every
    problem in the file. Never writes anything, never partially succeeds."""
    columns = _schema(source)

    # Excel writes a BOM on the first header cell, which would otherwise
    # make 'external_ref' arrive as '﻿external_ref' and read as a
    # missing required column.
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [cell.strip().lower() for cell in next(reader)]
    except StopIteration:
        raise IngestValidationError([_error(0, "", "file has no header row")])

    index = {name: i for i, name in enumerate(header)}
    missing = [
        _error(0, c.name, "required column is missing from the header")
        for c in columns if c.required and c.name not in index
    ]
    if missing:
        # No point reporting row-level problems against a header we can't
        # read the rows with.
        raise IngestValidationError(missing)

    errors: list[dict] = []
    rows: list[dict] = []
    seen_refs: dict[str, int] = {}

    for row_number, raw in enumerate(reader, start=1):
        # Blank lines still consume a row number so the numbers we report
        # keep lining up with the file the user is looking at.
        if not any(cell.strip() for cell in raw):
            continue

        if max_rows is not None and row_number > max_rows:
            # Bail the moment the ceiling is crossed rather than parsing the
            # rest to say so: the whole file is being rejected either way,
            # and the error list this function builds is itself unbounded
            # without this.
            raise IngestValidationError(
                [_error(0, "", f"file has more than {max_rows} data rows")])

        values = {}
        for column in columns:
            position = index.get(column.name)
            cell = raw[position].strip() if position is not None and position < len(raw) else ""
            if not cell:
                if column.required:
                    errors.append(_error(row_number, column.name, "required value is empty"))
                values[column.name] = None
                continue
            values[column.name] = _validate_cell(column, cell, row_number, errors)

        _check_row_rules(source, values, row_number, errors, seen_refs)
        rows.append(_to_transaction(source, values))

    if errors:
        raise IngestValidationError(errors)
    if not rows:
        raise IngestValidationError([_error(0, "", "file has no data rows")])
    return rows


def _validate_cell(column: Column, cell: str, row_number: int, errors: list[dict]):
    """The validated value, or None once the problem has been recorded."""
    if column.kind == "amount":
        try:
            amount = int(cell)
        except ValueError:
            errors.append(_error(row_number, column.name,
                                 f"must be a whole number of paise, got {cell!r}"))
            return None
        if amount <= 0:
            errors.append(_error(row_number, column.name,
                                 f"must be greater than zero, got {amount}"))
            return None
        return amount

    if column.kind == "timestamp":
        try:
            return _parse_timestamp(cell)
        except ValueError:
            errors.append(_error(row_number, column.name,
                                 f"must be an ISO 8601 date or datetime, got {cell!r}"))
            return None

    if column.kind == "choice":
        if cell not in column.allowed:
            errors.append(_error(
                row_number, column.name,
                f"must be one of {', '.join(column.allowed)}, got {cell!r}"))
            return None
        return cell

    return cell


def _check_row_rules(source: str, values: dict, row_number: int,
                      errors: list[dict], seen_refs: dict[str, int]) -> None:
    """Cross-column rules, checked after every cell in the row."""
    ref = values.get("external_ref")
    if ref is not None:
        # Registered even when the rest of the row is bad, so a later
        # duplicate of a bad row is still reported as a duplicate.
        first_seen = seen_refs.setdefault(ref, row_number)
        if first_seen != row_number:
            errors.append(_error(row_number, "external_ref",
                                 f"duplicate of row {first_seen} in this file"))

    transaction_type = values.get("transaction_type") or "payment"
    if source == "bank_statement" and transaction_type != "payment" \
            and not values.get("original_ref"):
        errors.append(_error(
            row_number, "original_ref",
            f"required for a {transaction_type} row, to trace what it reverses"))

    if source == "ledger":
        _check_fund_account(values, row_number, errors)


def _check_fund_account(values: dict, row_number: int, errors: list[dict]) -> None:
    """A payout instruction is all-or-nothing, per fund account type.

    Either the row carries no payout columns at all (reconcilable only), or it
    carries a complete, single-shaped instruction. The half-filled middle --
    an account number with no type, a bank_account with no IFSC -- is what
    produces a row that loads without complaint and then cannot pay anyone.
    """
    fund_type = values.get("fund_account_type")

    if not fund_type:
        if any(values.get(c) for c in _IMPLIES_FUND_ACCOUNT):
            errors.append(_error(
                row_number, "fund_account_type",
                "required when any fund_account or contact column is set"))
        return

    for column in _REQUIRED_BY_FUND_TYPE[fund_type]:
        if not values.get(column):
            errors.append(_error(
                row_number, column,
                f"required when fund_account_type is {fund_type!r}"))
    for column in _FORBIDDEN_BY_FUND_TYPE[fund_type]:
        if values.get(column):
            errors.append(_error(
                row_number, column,
                f"must be empty when fund_account_type is {fund_type!r}"))


def _to_transaction(source: str, values: dict) -> dict:
    row = {
        "source": source,
        # What lets the dispatch path refuse a CSV-supplied payout
        # instruction on production credentials. See app/router.py.
        "origin": "upload",
        "external_ref": values["external_ref"],
        "amount_paise": values["amount_paise"],
        "occurred_at": values["occurred_at"],
        "counterparty": values.get("counterparty"),
        "narration": values.get("narration"),
    }
    if source == "ledger":
        # The ledger is the expected side: every row is a payment, and it
        # correlates on its own id -- the same shape generate_dataset()
        # produces, so uploaded and generated ledgers match identically.
        row["reference_id"] = values["external_ref"]
        row["transaction_type"] = "payment"
        row["original_ref"] = None
        # None when absent, which load_transactions writes as NULL -- exactly
        # what a reconcile-only row should carry.
        for column in _PAYOUT_COLUMNS:
            row[column.name] = values.get(column.name)
    else:
        row["reference_id"] = values.get("reference_id")
        row["transaction_type"] = values.get("transaction_type") or "payment"
        row["original_ref"] = values.get("original_ref")
    return row


# Which exceptions a replace of each source invalidates.
#
# 'ledger' clears every exception that names a ledger row, whatever it was
# matched against -- the expected side just changed underneath all of them.
# It deliberately spares an orphan (ledger_ref IS NULL), which says "money
# moved that the ledger never asked for": replacing the ledger doesn't
# explain that away, it is exactly the kind of thing that should survive.
#
# 'bank_statement' is scoped by matched_source instead, so re-uploading a
# statement leaves the gateway-matched half of the backlog untouched.
_EXCEPTION_SCOPE = {
    "ledger": "ledger_ref IS NOT NULL",
    "bank_statement": "matched_source = 'bank_statement'",
}


def _counts(conn: sqlite3.Connection, source: str) -> dict:
    """What a replace of `source` is about to destroy."""
    scope = _EXCEPTION_SCOPE[source]
    doomed_keys = f"SELECT exception_key FROM exceptions WHERE {scope}"

    def count(sql: str, params: tuple = ()) -> int:
        return conn.execute(sql, params).fetchone()[0]

    return {
        "rows": count("SELECT COUNT(*) FROM transactions WHERE source = ?", (source,)),
        "exceptions": count(f"SELECT COUNT(*) FROM exceptions WHERE {scope}"),
        "actions": count(
            f"SELECT COUNT(*) FROM actions WHERE exception_key IN ({doomed_keys})"),
        # An action carrying a gateway_payout_id already moved real money.
        # Deleting its record is not the same as dropping a queued attempt,
        # and the confirmation step has to be able to say so out loud.
        "live_payouts": count(
            f"""SELECT COUNT(*) FROM actions
                WHERE gateway_payout_id IS NOT NULL
                  AND exception_key IN ({doomed_keys})"""),
    }


def summarize(conn: sqlite3.Connection) -> dict:
    """What each uploadable source currently holds -- read on the Data
    tab's mount so it isn't blank after a reload."""
    out = {}
    for source in SOURCES:
        row = conn.execute(
            "SELECT COUNT(*) AS rows, MAX(created_at) AS updated_at "
            "FROM transactions WHERE source = ?",
            (source,),
        ).fetchone()
        out[source] = {"rows": row[0], "updated_at": row[1]}
    return out


def preview_replace(conn: sqlite3.Connection, source: str, rows: list[dict]) -> dict:
    """What replace() would do, without doing it -- the same shape as
    GET /pipeline/route/preview, and for the same reason: a destructive
    action should state its blast radius before a human commits to it.
    Read-only; issues no writes at all."""
    _schema(source)
    counts = _counts(conn, source)
    return {
        "rows_to_load": len(rows),
        "rows_to_delete": counts["rows"],
        "exceptions_to_delete": counts["exceptions"],
        "actions_to_delete": counts["actions"],
        "live_payouts_affected": counts["live_payouts"],
    }


def replace(conn: sqlite3.Connection, source: str, rows: list[dict]) -> dict:
    """Swap out everything for one source, in a single transaction.

    Replace rather than append, because a re-uploaded file is a corrected
    view of the same period far more often than it is new data -- appending
    would silently double every row that appears in both.

    The cascade is a consequence of that: an exception derived from rows
    that no longer exist is not evidence of anything, and its actions
    reference an exception_key that is about to disappear (they carry a
    foreign key to it). Deleting them is the honest outcome; leaving them
    would show a backlog computed against data nobody can see any more.
    """
    _schema(source)
    scope = _EXCEPTION_SCOPE[source]
    counts = _counts(conn, source)
    try:
        # Actions first: they hold a foreign key into exceptions.
        conn.execute(
            f"""DELETE FROM actions WHERE exception_key IN
                (SELECT exception_key FROM exceptions WHERE {scope})""")
        conn.execute(f"DELETE FROM exceptions WHERE {scope}")
        conn.execute("DELETE FROM transactions WHERE source = ?", (source,))
        load_transactions(conn, rows)
        result = {
            "rows_loaded": len(rows),
            "rows_deleted": counts["rows"],
            "exceptions_deleted": counts["exceptions"],
            "actions_deleted": counts["actions"],
        }
        # subject_id is the source rather than a row id: the event is about
        # the whole source being swapped, and the Activity log tab renders
        # it alongside every other pipeline stage with no special casing.
        log_audit(conn, actor="ingest", subject_type="transaction",
                  subject_id=source, event="data_replaced",
                  detail=json.dumps(result))
        conn.commit()
    except Exception:
        # Half a replace is worse than none: it would leave the source
        # emptied and nothing loaded in its place.
        conn.rollback()
        raise
    return result


def build_template(source: str) -> str:
    """A header row plus one example row, generated from the same schema
    table the parser reads -- so a downloaded template always parses."""
    columns = _schema(source)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([c.name for c in columns])
    writer.writerow([c.example for c in columns])
    return out.getvalue()
