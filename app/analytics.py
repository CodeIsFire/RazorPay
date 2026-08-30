"""Exception intelligence -- "what should I chase first?", computed straight
from the DB the same way app/funnel.py computes the headline funnel.

Everything here describes NON-TERMINAL exceptions only ('open' + 'pending'),
because the question is what still needs a human or another dispatch.
'resolved'/'abandoned' rows are history, not backlog.

Two decisions in here are load-bearing:

1. AGE IS MEASURED FROM THE TRANSACTION'S occurred_at, NOT exceptions.created_at.

   created_at records when our pipeline last ran, and a demo (or a fresh
   reload) creates every exception inside the same second -- bucketing on it
   puts the entire backlog in "under 1d" and says nothing at all. occurred_at
   is when the money actually moved, which is both genuinely spread out and
   the more honest question: "how long has this been unreconciled?" rather
   than "how long since I ran reconcile?".

   The oldest bucket's boundary is config.MAX_EXCEPTION_AGE_DAYS -- the same
   constant app/router.py abandons on -- so "past the bound" here and
   "abandoned by the router" there can never drift apart.

2. AN EXCEPTION IS ANCHORED TO A TRANSACTION BY LEDGER REF *OR* GATEWAY REF.

   A plain `exceptions JOIN transactions ON ledger_ref` silently drops rows
   and understates the money at risk, in two ways that both occur in real
   data:

     * 'unexplained' exceptions have NO ledger_ref at all -- they are
       gateway-side orphans (a transaction the ledger never expected), and
       gateway_ref is their only handle.
     * a batch-kind exception's ledger_ref is a comma-joined list of every
       ledger row in the batch (see app/classify.py), which matches no single
       external_ref.

   _first_ref() + the ledger-then-actual fallback below handle both. The
   invariants at the bottom of this docstring are what keep it honest.

INVARIANTS (asserted in tests/test_analytics.py):
    sum(by_cause values)                        == value_at_risk_paise
    sum(by_age values) + undated_paise          == value_at_risk_paise
    sum(by_cause counts)                        == exception_count
    sum(by_age counts) + undated_count          == exception_count
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.config import MAX_EXCEPTION_AGE_DAYS

# Backlog, not history. Terminal rows are excluded everywhere in this module.
NON_TERMINAL_STATUSES = ("open", "pending")

TOP_COUNTERPARTY_LIMIT = 8


def _first_ref(ref: str | None) -> str | None:
    """A batch-kind exception's ledger_ref is 'LED-1,LED-2,LED-3'; every other
    kind is a single ref. Take the first either way -- same convention as
    db.fetch_payout_instruction, so the two can't disagree about which row an
    exception belongs to."""
    if not ref:
        return None
    first = ref.split(",")[0].strip()
    return first or None


def _parse_ts(ts: str | None) -> datetime | None:
    """transactions.occurred_at is ISO 8601 from the fixtures
    ('2026-08-10T09:00:00+00:00') but SQLite's own datetime() default writes
    '2026-08-23 15:48:56'. Accept both, and treat a naive timestamp as UTC --
    every writer in this app is UTC, and guessing local time here would shift
    every age by hours."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.strip().replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_buckets() -> list[tuple[str, float | None]]:
    """(label, exclusive upper bound in days). The final bucket is unbounded
    and is exactly the router's abandonment threshold, so the UI's "past the
    bound" and router.py's actual behaviour stay in lockstep."""
    return [
        ("under 1d", 1.0),
        ("1-3d", 3.0),
        (f"3-{MAX_EXCEPTION_AGE_DAYS}d", float(MAX_EXCEPTION_AGE_DAYS)),
        (f"over {MAX_EXCEPTION_AGE_DAYS}d", None),
    ]


def _anchor_index(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """external_ref -> (occurred_at, counterparty), split by whether the row
    is the ledger's own view or an actual-side one. Two dicts rather than one
    because a ledger ref and a gateway ref could in principle collide, and
    resolving the ledger side first is what makes the fallback deterministic."""
    ledger: dict[str, tuple] = {}
    actual: dict[str, tuple] = {}
    for row in conn.execute(
        "SELECT source, external_ref, occurred_at, counterparty FROM transactions"
    ):
        target = ledger if row["source"] == "ledger" else actual
        # First writer wins: duplicates on the actual side are themselves an
        # exception cause, and either copy dates the row identically enough.
        target.setdefault(row["external_ref"], (row["occurred_at"], row["counterparty"]))
    return ledger, actual


def compute_exception_intelligence(conn: sqlite3.Connection, *,
                                    now: datetime | None = None) -> dict:
    """Backlog broken down by cause, by age, and by counterparty.

    `now` is injectable so tests can assert bucket boundaries against a fixed
    clock instead of racing the wall.
    """
    now = now or datetime.now(timezone.utc)
    ledger_index, actual_index = _anchor_index(conn)

    placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
    rows = conn.execute(
        f"""SELECT exception_key, cause, amount_paise, ledger_ref, gateway_ref
            FROM exceptions WHERE status IN ({placeholders})""",
        NON_TERMINAL_STATUSES,
    ).fetchall()

    buckets = _age_buckets()
    by_cause: dict[str, dict] = {}
    by_age = {label: {"bucket": label, "count": 0, "amount_paise": 0} for label, _ in buckets}
    by_counterparty: dict[str, dict] = {}
    undated_count = 0
    undated_paise = 0
    total_paise = 0

    for row in rows:
        amount = row["amount_paise"]
        total_paise += amount

        cause = by_cause.setdefault(
            row["cause"], {"cause": row["cause"], "count": 0, "amount_paise": 0}
        )
        cause["count"] += 1
        cause["amount_paise"] += amount

        # Ledger side first, then the actual side -- an 'unexplained' orphan
        # has only the latter, and dropping it here would quietly shrink the
        # money at risk.
        anchor = ledger_index.get(_first_ref(row["ledger_ref"]) or "")
        if anchor is None:
            anchor = actual_index.get(_first_ref(row["gateway_ref"]) or "")

        occurred_at = _parse_ts(anchor[0]) if anchor else None
        counterparty = (anchor[1] if anchor else None) or "Unattributed"

        party = by_counterparty.setdefault(
            counterparty, {"counterparty": counterparty, "count": 0, "amount_paise": 0}
        )
        party["count"] += 1
        party["amount_paise"] += amount

        if occurred_at is None:
            # Can't be dated, so it can't be bucketed. Counted separately
            # rather than folded into the oldest bucket, which would invent
            # an age the data doesn't support.
            undated_count += 1
            undated_paise += amount
            continue

        age_days = (now - occurred_at).total_seconds() / 86400.0
        for label, upper in buckets:
            if upper is None or age_days < upper:
                by_age[label]["count"] += 1
                by_age[label]["amount_paise"] += amount
                break

    def _share(amount: int) -> float:
        return round(amount / total_paise, 4) if total_paise else 0.0

    causes = sorted(by_cause.values(), key=lambda c: c["amount_paise"], reverse=True)
    for cause in causes:
        cause["share"] = _share(cause["amount_paise"])

    parties = sorted(by_counterparty.values(), key=lambda p: p["amount_paise"], reverse=True)

    aged = [by_age[label] for label, _ in buckets]
    # The UI colours only this bucket as critical; naming it here keeps that
    # decision in one place rather than string-matching a label in JS.
    past_bound_label = buckets[-1][0]
    for bucket in aged:
        bucket["past_bound"] = bucket["bucket"] == past_bound_label
        bucket["share"] = _share(bucket["amount_paise"])

    return {
        "exception_count": len(rows),
        "value_at_risk_paise": total_paise,
        "max_exception_age_days": MAX_EXCEPTION_AGE_DAYS,
        "by_cause": causes,
        "by_age": aged,
        "undated_count": undated_count,
        "undated_paise": undated_paise,
        "top_counterparties": parties[:TOP_COUNTERPARTY_LIMIT],
    }


def compute_daily_reconciliation(conn: sqlite3.Connection) -> list[dict]:
    """Ledger value per business day, split into what reconciled and what is
    still outstanding -- "which days' payouts are still stuck?".

    Business days come from transactions.occurred_at for the same reason ages
    do (see the module docstring): the pipeline's own timestamps are all one
    instant and would collapse this to a single column.

    A ledger row counts as outstanding when it is the subject of a
    non-terminal exception. Gateway-side orphans ('unexplained') have no
    ledger row and therefore no business day, so they are deliberately absent
    here -- they are counted by compute_exception_intelligence(), which is
    where the total value at risk lives. These two functions answer different
    questions and their totals are not meant to agree.
    """
    outstanding_refs: set[str] = set()
    placeholders = ",".join("?" for _ in NON_TERMINAL_STATUSES)
    for row in conn.execute(
        f"SELECT ledger_ref FROM exceptions WHERE status IN ({placeholders})",
        NON_TERMINAL_STATUSES,
    ):
        # A batch exception holds every ledger row in the batch, so all of
        # them are outstanding -- not just the first, which is the one case
        # where _first_ref would be the wrong tool.
        for ref in (row["ledger_ref"] or "").split(","):
            ref = ref.strip()
            if ref:
                outstanding_refs.add(ref)

    days: dict[str, dict] = {}
    for row in conn.execute(
        """SELECT external_ref, amount_paise, occurred_at
           FROM transactions WHERE source='ledger' ORDER BY occurred_at"""
    ):
        occurred = _parse_ts(row["occurred_at"])
        if occurred is None:
            continue
        key = occurred.date().isoformat()
        day = days.setdefault(
            key, {"day": key, "total_paise": 0, "outstanding_paise": 0,
                  "reconciled_paise": 0, "count": 0}
        )
        day["count"] += 1
        day["total_paise"] += row["amount_paise"]
        if row["external_ref"] in outstanding_refs:
            day["outstanding_paise"] += row["amount_paise"]
        else:
            day["reconciled_paise"] += row["amount_paise"]

    return [days[k] for k in sorted(days)]
