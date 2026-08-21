"""The reconciliation engine: exact match first, fuzzy match fallback.

Deliberately pure — reconcile() takes plain row dicts (the same shape as
app/fixtures.py produces or a DB row cast to dict) and returns plain data.
No SQLite here, so it's trivial to unit-test against the M1 fixture and
its ground truth without touching a database at all. reconcile_from_db()
is the thin DB-facing wrapper used by the rest of the app.

Two tiers, both gated by the same time window so a "match" always means
"same transaction, and it behaved normally" — not just "same identity,
eventually":

  Tier 1 (exact):  same reference_id, exact amount, within the time window.
  Tier 2 (fuzzy):  reference_id match (or missing reference_id + same
                    counterparty), amount within a small rounding
                    tolerance, still within the time window.

Anything a real fee deduction or a multi-day settlement delay would
violate is deliberately NOT swallowed by tier 2 — those are supposed to
surface as exceptions (fee_mismatch, timing_lag) for M3 to classify, not
get silently matched away. When more than one gateway row is a valid
candidate (the duplicate-payout case), the earliest one is treated as the
real match and the rest are left unmatched surplus, which is exactly the
signal M3 needs to call them duplicates.
"""
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3

from app.config import FUZZY_AMOUNT_TOLERANCE_PAISE, FUZZY_TIME_WINDOW_HOURS
from app.db import log_audit


@dataclass
class MatchResult:
    matches: list = field(default_factory=list)          # list[(ledger_row, gateway_row, tier)]
    unmatched_ledger: list = field(default_factory=list)  # list[ledger_row]
    unmatched_gateway: list = field(default_factory=list)  # list[gateway_row]


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _within_window(t1: datetime, t2: datetime, hours: int) -> bool:
    return abs((t1 - t2).total_seconds()) <= hours * 3600


def reconcile(ledger_rows: list[dict], gateway_rows: list[dict]) -> MatchResult:
    # Sort so tie-breaks (earliest gateway row wins) are deterministic
    # regardless of the order rows were fetched/generated in.
    ledger_rows = sorted(ledger_rows, key=lambda r: (r["occurred_at"], r["external_ref"]))
    gateway_rows = sorted(gateway_rows, key=lambda r: (r["occurred_at"], r["external_ref"]))

    claimed: set[str] = set()  # gateway external_refs already matched
    matches = []
    round1_unmatched = []

    # Tier 1: exact match.
    for led in ledger_rows:
        candidates = [
            gw for gw in gateway_rows
            if gw["external_ref"] not in claimed
            and gw["reference_id"] == led["reference_id"]
            and gw["amount_paise"] == led["amount_paise"]
            and _within_window(_parse_time(gw["occurred_at"]), _parse_time(led["occurred_at"]),
                                FUZZY_TIME_WINDOW_HOURS)
        ]
        if candidates:
            chosen = candidates[0]  # already sorted by occurred_at -> earliest wins
            claimed.add(chosen["external_ref"])
            matches.append((led, chosen, "exact"))
        else:
            round1_unmatched.append(led)

    # Tier 2: fuzzy fallback, only for what tier 1 couldn't place.
    unmatched_ledger = []
    for led in round1_unmatched:
        candidates = [
            gw for gw in gateway_rows
            if gw["external_ref"] not in claimed
            and (
                gw["reference_id"] == led["reference_id"]
                or (not gw["reference_id"] and gw["counterparty"] == led["counterparty"])
            )
            and abs(gw["amount_paise"] - led["amount_paise"]) <= FUZZY_AMOUNT_TOLERANCE_PAISE
            and _within_window(_parse_time(gw["occurred_at"]), _parse_time(led["occurred_at"]),
                                FUZZY_TIME_WINDOW_HOURS)
        ]
        if candidates:
            chosen = min(candidates, key=lambda g: (abs(g["amount_paise"] - led["amount_paise"]), g["occurred_at"]))
            claimed.add(chosen["external_ref"])
            matches.append((led, chosen, "fuzzy"))
        else:
            unmatched_ledger.append(led)

    unmatched_gateway = [gw for gw in gateway_rows if gw["external_ref"] not in claimed]

    return MatchResult(matches=matches, unmatched_ledger=unmatched_ledger,
                        unmatched_gateway=unmatched_gateway)


def reconcile_from_db(conn: sqlite3.Connection) -> MatchResult:
    ledger_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE source='ledger'"
    )]
    gateway_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE source='gateway'"
    )]
    result = reconcile(ledger_rows, gateway_rows)
    log_audit(
        conn, actor="reconciler", subject_type="run", subject_id="reconcile_from_db",
        event="reconciliation_complete",
        detail=(f"matched={len(result.matches)} "
                f"unmatched_ledger={len(result.unmatched_ledger)} "
                f"unmatched_gateway={len(result.unmatched_gateway)}"),
    )
    conn.commit()
    return result
