"""Loads generated fixture rows into the `transactions` table."""
import sqlite3


def load_transactions(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO transactions
            (source, external_ref, reference_id, amount_paise, currency,
             counterparty, narration, occurred_at, raw_json)
        VALUES
            (:source, :external_ref, :reference_id, :amount_paise, :currency,
             :counterparty, :narration, :occurred_at, :raw_json)
        """,
        rows,
    )


def load_dataset_into_db(conn: sqlite3.Connection, ledger_rows: list[dict],
                          gateway_rows: list[dict]) -> None:
    load_transactions(conn, ledger_rows)
    load_transactions(conn, gateway_rows)
    conn.commit()
