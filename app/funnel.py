"""Computes the dashboard's headline funnel straight from the DB --
ingested -> matched -> exceptions -> recovered -- plus one honest extra
the four-stage funnel has no room for: gateway-side anomalies (duplicates,
unexplained transactions). Those don't correspond to a ledger transaction
failing to reconcile -- the ledger row in a duplicate case matched fine --
so folding them into "exceptions" would misrepresent what actually needs
attention.

matched + exceptions + recovered == ingested, always. A ledger row's fate
is decided once at reconciliation time (matched, or not); an unmatched row
can only ever move from "exceptions" to "recovered", never back to
"matched". That identity is asserted in tests/test_funnel.py so a future
bug here fails loudly instead of quietly skewing the demo numbers.
"""
import sqlite3

LEDGER_SIDE_CAUSES = ("failed_payment", "fee_mismatch", "timing_lag")
GATEWAY_SIDE_CAUSES = ("duplicate", "unexplained")


def compute_funnel(conn: sqlite3.Connection) -> dict:
    ingested = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source='ledger'"
    ).fetchone()[0]

    placeholders = ",".join("?" for _ in LEDGER_SIDE_CAUSES)

    exceptions_open = conn.execute(
        f"""SELECT COUNT(DISTINCT ledger_ref) FROM exceptions
            WHERE cause IN ({placeholders}) AND status != 'resolved'""",
        LEDGER_SIDE_CAUSES,
    ).fetchone()[0]

    recovered = conn.execute(
        f"""SELECT COUNT(DISTINCT ledger_ref) FROM exceptions
            WHERE cause IN ({placeholders}) AND status = 'resolved'""",
        LEDGER_SIDE_CAUSES,
    ).fetchone()[0]

    matched = ingested - exceptions_open - recovered

    amount_recovered_paise = conn.execute(
        f"""SELECT COALESCE(SUM(amount_paise), 0) FROM exceptions
            WHERE cause IN ({placeholders}) AND status = 'resolved'""",
        LEDGER_SIDE_CAUSES,
    ).fetchone()[0]

    gw_placeholders = ",".join("?" for _ in GATEWAY_SIDE_CAUSES)
    gateway_side_anomalies = conn.execute(
        f"SELECT COUNT(*) FROM exceptions WHERE cause IN ({gw_placeholders})",
        GATEWAY_SIDE_CAUSES,
    ).fetchone()[0]

    return {
        "ingested": ingested,
        "matched": matched,
        "exceptions": exceptions_open,
        "recovered": recovered,
        "match_rate": round(matched / ingested, 4) if ingested else None,
        "amount_recovered_paise": amount_recovered_paise,
        "gateway_side_anomalies": gateway_side_anomalies,
    }
