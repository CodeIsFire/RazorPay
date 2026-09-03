"""Loads generated fixture rows into the `transactions` table."""
import sqlite3


def load_transactions(conn: sqlite3.Connection, rows: list[dict]) -> None:
    # Built as explicit tuples with .get() defaults, not executemany(dict)
    # keyed on the SQL's named placeholders -- that would require every row
    # dict to carry transaction_type/original_ref/settlement_batch_id,
    # breaking every existing ledger/gateway row from generate_dataset()
    # (and every hand-built test fixture) that predates those columns.
    conn.executemany(
        """
        INSERT INTO transactions
            (source, external_ref, reference_id, amount_paise, currency,
             counterparty, transaction_type, original_ref, settlement_batch_id,
             narration, occurred_at,
             payout_purpose, payout_mode, fund_account_type, fund_account_name,
             fund_account_ifsc, fund_account_number, fund_account_vpa,
             contact_type, contact_email, contact_mobile, contact_address,
             contact_city, contact_zipcode, contact_state, notes,
             origin, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?)
        """,
        [
            (
                r["source"], r["external_ref"], r.get("reference_id"),
                r["amount_paise"], r.get("currency", "INR"), r.get("counterparty"),
                r.get("transaction_type", "payment"), r.get("original_ref"),
                r.get("settlement_batch_id"),
                r.get("narration"), r["occurred_at"],
                # The RazorpayX payout half. Every one .get()s to None so a
                # gateway/bank_statement row -- and every hand-built test
                # fixture predating these columns -- still loads unchanged.
                r.get("payout_purpose"), r.get("payout_mode"),
                r.get("fund_account_type"), r.get("fund_account_name"),
                r.get("fund_account_ifsc"), r.get("fund_account_number"),
                r.get("fund_account_vpa"),
                r.get("contact_type"), r.get("contact_email"),
                r.get("contact_mobile"), r.get("contact_address"),
                r.get("contact_city"), r.get("contact_zipcode"),
                r.get("contact_state"), r.get("notes"),
                # Absent means generated: every fixture row and every
                # hand-built test row predates this column.
                r.get("origin", "generated"), r.get("raw_json"),
            )
            for r in rows
        ],
    )


def load_dataset_into_db(conn: sqlite3.Connection, ledger_rows: list[dict],
                          gateway_rows: list[dict]) -> None:
    load_transactions(conn, ledger_rows)
    load_transactions(conn, gateway_rows)
    conn.commit()
