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

Two further passes run after tiers 1/2, over whatever they left unmatched:

  Split pass (1 ledger -> N gateway):  a ledger row with 2+ still-unclaimed
    gateway candidates sharing its reference_id -- exactly one candidate is
    tier 1/2's fee_mismatch/timing_lag territory, untouched here -- whose
    amounts sum within tolerance of the ledger amount. This is what
    disambiguates a legitimate split disbursement from a duplicate payout:
    a real duplicate's second row repeats the FULL amount and gets claimed
    by tier 1 before this pass ever runs, leaving only the surplus row
    behind for classify.py's 'duplicate' cause. This pass only ever sees
    rows that individually look like partial amounts.

  Batch pass (N ledger -> 1 gateway):  ledger rows sharing a
    settlement_batch_id, settled by one still-unclaimed gateway row whose
    reference_id equals that batch id (not any single ledger row's own
    id), summing within tolerance of the gateway amount.

Either pass, when the sum falls short (or overshoots) instead of landing
within tolerance, produces a `partial_groups` entry instead of a match --
still unresolved, but the group is not spread back into
unmatched_ledger/unmatched_gateway as independent rows, so classify.py
sees one coherent 'partial_payment' cause per group rather than several
disconnected, misleading ones (e.g. a single-candidate fee_mismatch
picked arbitrarily from a 3-way split).
"""
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3

from app.config import FUZZY_AMOUNT_TOLERANCE_PAISE, FUZZY_TIME_WINDOW_HOURS
from app.db import log_audit


@dataclass
class MatchResult:
    matches: list = field(default_factory=list)          # list[(ledger_row, gateway_row, tier)]
    split_matches: list = field(default_factory=list)     # list[{"ledger": row, "gateways": [row,...]}]
    batch_matches: list = field(default_factory=list)     # list[{"ledgers": [row,...], "gateway": row}]
    partial_groups: list = field(default_factory=list)    # list[{"kind": "split"|"batch", "ledgers": [...], "gateways": [...]}]
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

    # --- Split pass: 1 ledger row disbursed across 2+ gateway rows ---
    split_matches = []
    partial_groups = []

    unmatched_gw_by_ref: dict[str | None, list[dict]] = {}
    for gw in unmatched_gateway:
        unmatched_gw_by_ref.setdefault(gw["reference_id"], []).append(gw)

    split_claimed_gw: set[str] = set()
    still_unmatched_ledger = []
    for led in unmatched_ledger:
        candidates = unmatched_gw_by_ref.get(led["reference_id"])
        if not candidates or len(candidates) < 2:
            still_unmatched_ledger.append(led)
            continue

        total = sum(g["amount_paise"] for g in candidates)
        in_window = all(
            _within_window(_parse_time(g["occurred_at"]), _parse_time(led["occurred_at"]),
                            FUZZY_TIME_WINDOW_HOURS)
            for g in candidates
        )
        if abs(total - led["amount_paise"]) <= FUZZY_AMOUNT_TOLERANCE_PAISE and in_window:
            split_matches.append({"ledger": led, "gateways": candidates})
        else:
            partial_groups.append({"kind": "split", "ledgers": [led], "gateways": candidates})
        for g in candidates:
            split_claimed_gw.add(g["external_ref"])
        # Consumed -- a second ledger row that somehow shares this
        # reference_id (not expected in practice) must not reprocess the
        # same candidates as its own group.
        del unmatched_gw_by_ref[led["reference_id"]]

    unmatched_ledger = still_unmatched_ledger
    unmatched_gateway = [gw for gw in unmatched_gateway if gw["external_ref"] not in split_claimed_gw]

    # --- Batch pass: 2+ ledger rows sharing settlement_batch_id, settled
    # by one gateway row whose reference_id is that batch id ---
    batch_matches = []

    ledgers_by_batch: dict[str, list[dict]] = {}
    for led in unmatched_ledger:
        batch_id = led.get("settlement_batch_id")
        if batch_id:
            ledgers_by_batch.setdefault(batch_id, []).append(led)

    batch_claimed_led: set[str] = set()
    batch_claimed_gw: set[str] = set()
    for gw in unmatched_gateway:
        led_group = ledgers_by_batch.get(gw["reference_id"])
        if not led_group:
            continue

        total = sum(l["amount_paise"] for l in led_group)
        in_window = all(
            _within_window(_parse_time(gw["occurred_at"]), _parse_time(l["occurred_at"]),
                            FUZZY_TIME_WINDOW_HOURS)
            for l in led_group
        )
        if abs(total - gw["amount_paise"]) <= FUZZY_AMOUNT_TOLERANCE_PAISE and in_window:
            batch_matches.append({"ledgers": led_group, "gateway": gw})
        else:
            partial_groups.append({"kind": "batch", "ledgers": led_group, "gateways": [gw]})
        for l in led_group:
            batch_claimed_led.add(l["external_ref"])
        batch_claimed_gw.add(gw["external_ref"])
        # Consumed -- a second gateway row that somehow shares this batch id
        # (e.g. a duplicate batch settlement) must not reclaim the same
        # ledger group; it falls through to unmatched_gateway untouched.
        del ledgers_by_batch[gw["reference_id"]]

    unmatched_ledger = [l for l in unmatched_ledger if l["external_ref"] not in batch_claimed_led]
    unmatched_gateway = [g for g in unmatched_gateway if g["external_ref"] not in batch_claimed_gw]

    return MatchResult(matches=matches, split_matches=split_matches, batch_matches=batch_matches,
                        partial_groups=partial_groups, unmatched_ledger=unmatched_ledger,
                        unmatched_gateway=unmatched_gateway)


ACTUAL_SOURCES = ("gateway", "bank_statement")


def reconcile_from_db(conn: sqlite3.Connection, actual_source: str = "gateway") -> MatchResult:
    """actual_source picks which non-ledger source to reconcile the ledger
    against -- 'gateway' (RazorpayX test-mode transactions, the original
    and default source) or 'bank_statement' (a real bank statement, loaded
    into the same `transactions` table with source='bank_statement'). Each
    call reconciles the ledger against exactly one actual source; running
    against two sources means calling this twice, once per source -- see
    app/classify.py's `source` param for how the resulting exceptions stay
    labeled with which one produced them."""
    if actual_source not in ACTUAL_SOURCES:
        raise ValueError(f"actual_source must be one of {ACTUAL_SOURCES}, got {actual_source!r}")

    ledger_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE source='ledger'"
    )]
    actual_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE source=?", (actual_source,)
    )]
    result = reconcile(ledger_rows, actual_rows)
    log_audit(
        conn, actor="reconciler", subject_type="run", subject_id="reconcile_from_db",
        event="reconciliation_complete",
        detail=(f"actual_source={actual_source} matched={len(result.matches)} "
                f"split={len(result.split_matches)} batch={len(result.batch_matches)} "
                f"partial_groups={len(result.partial_groups)} "
                f"unmatched_ledger={len(result.unmatched_ledger)} "
                f"unmatched_gateway={len(result.unmatched_gateway)}"),
    )
    conn.commit()
    return result
