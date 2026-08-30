"""Computes the dashboard's headline funnel straight from the DB --
ingested -> matched -> exceptions -> recovered -- plus one honest extra
the four-stage funnel has no room for: gateway-side anomalies (duplicates,
unexplained transactions, and unmatched refunds/chargebacks). Those don't
correspond to a ledger transaction failing to reconcile -- the ledger row
in a duplicate case matched fine, and a refund/chargeback is a legitimate
gateway-originated event the ledger just hasn't caught up to yet -- so
folding them into "exceptions" would misrepresent what actually needs
attention.

matched + exceptions + recovered == ingested, always. A ledger row's fate
is decided once at reconciliation time (matched, or not); an unmatched row
can only ever move from "exceptions" to "recovered", never back to
"matched". That identity is asserted in tests/test_funnel.py so a future
bug here fails loudly instead of quietly skewing the demo numbers.
"""
import sqlite3

LEDGER_SIDE_CAUSES = ("failed_payment", "fee_mismatch", "timing_lag", "partial_payment")
GATEWAY_SIDE_CAUSES = ("duplicate", "unexplained", "refund_unmatched", "chargeback")


def _refs(conn: sqlite3.Connection, causes: tuple[str, ...], resolved: bool) -> set[str]:
    """The distinct ledger refs with (or without) a resolved exception.

    COUNT(DISTINCT ledger_ref) would undercount a batch-kind partial_payment
    row: its ledger_ref is a comma-joined list of every ledger row in the batch
    (see classify.py), and SQL DISTINCT treats that whole string as one value
    instead of N. Expanding in Python is the one place this funnel can't stay
    pure SQL."""
    placeholders = ",".join("?" for _ in causes)
    op = "=" if resolved else "!="
    rows = conn.execute(
        f"SELECT ledger_ref FROM exceptions WHERE cause IN ({placeholders}) AND status {op} 'resolved'",
        causes,
    ).fetchall()
    refs: set[str] = set()
    for row in rows:
        ledger_ref = row[0]
        if ledger_ref:
            refs.update(ledger_ref.split(","))
    return refs


def _open_and_recovered(conn: sqlite3.Connection, causes: tuple[str, ...]) -> tuple[set[str], set[str]]:
    """The two buckets, forced disjoint.

    One ledger row can raise several exceptions with different causes -- a
    fee_mismatch and a timing_lag against the same LED- ref is ordinary. Closing
    one of them does not reconcile the row, so a ref with ANY unresolved
    ledger-side exception belongs in 'exceptions', never in 'recovered'.

    Counting the two sets independently let six refs sit in both, and because
    matched = ingested - exceptions - recovered, every overlapping ref was
    subtracted twice: the funnel reported 22 rows matched cleanly when 28 had,
    and the headline match rate read 33.3% instead of 42.4%. The
    matched+exceptions+recovered == ingested identity still held, which is
    exactly why it went unnoticed -- the sum was right while two of its three
    terms were wrong.
    """
    open_refs = _refs(conn, causes, resolved=False)
    recovered_refs = _refs(conn, causes, resolved=True) - open_refs
    return open_refs, recovered_refs


def compute_funnel(conn: sqlite3.Connection) -> dict:
    ingested = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE source='ledger'"
    ).fetchone()[0]

    placeholders = ",".join("?" for _ in LEDGER_SIDE_CAUSES)

    open_refs, recovered_refs = _open_and_recovered(conn, LEDGER_SIDE_CAUSES)
    exceptions_open = len(open_refs)
    recovered = len(recovered_refs)

    # Safe now that the two sets are disjoint -- see _open_and_recovered.
    matched = ingested - exceptions_open - recovered

    # Deliberately NOT filtered by the disjoint sets above: this is money, not
    # rows. If a ref's fee_mismatch was resolved while its timing_lag is still
    # open, that fee really was recovered even though the row is not yet fully
    # reconciled -- so the ref counts as an exception above while its resolved
    # amount counts here.
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
