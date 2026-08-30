"""Fails if the payout sync logic has changed.

The sync path is deliberately frozen: it took several rounds to get correct
against live RazorpayX, and four of its properties are load-bearing and easy to
break by accident --

  1. The sweep watches queued/processing PLUS processed inside the reversal
     window. Narrowing it back to in-flight only re-introduces the bug where a
     post-settlement reversal is never detected and returned money keeps
     counting as recovered.
  2. 'reversed' is never re-read, so a reversal cannot flap back to processed.
  3. The no-op guard stops every settled payout being re-confirmed and
     re-audited on every 5s pass.
  4. The timer runs the sweep in a thread; the RazorpayX client is synchronous
     httpx and issues one request per watched payout, so inline it would stall
     every dashboard request for the length of the sweep.

This compares a hash of that surface against a recorded baseline. It is a
tripwire, not a lock -- if you intend to change the sync, update BASELINE in the
same commit so the change is deliberate and visible in review.

Usage: python -m scripts.check_sync_frozen
"""
import hashlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, main, router  # noqa: E402

BASELINE = "ffea62b66db336497cf45a39655bedc5d448bd5c98dde0a5e513918765503a54"


def parts() -> dict[str, str]:
    return {
        "router.sync_payout_statuses": inspect.getsource(router.sync_payout_statuses),
        "router.confirm_action": inspect.getsource(router.confirm_action),
        "main._sync_payouts_on_a_timer": inspect.getsource(main._sync_payouts_on_a_timer),
        "router.IN_FLIGHT_PAYOUT_STATUSES": repr(router.IN_FLIGHT_PAYOUT_STATUSES),
        "router.TERMINAL_PAYOUT_OUTCOME": repr(sorted(router.TERMINAL_PAYOUT_OUTCOME.items())),
        "config.SYNC_PAYOUTS_INTERVAL_SECONDS": repr(config.SYNC_PAYOUTS_INTERVAL_SECONDS),
        "config.SYNC_REVERSAL_WINDOW_DAYS": repr(config.SYNC_REVERSAL_WINDOW_DAYS),
    }


def combined(p: dict[str, str]) -> str:
    return hashlib.sha256("".join(p.values()).encode()).hexdigest()


def main_() -> int:
    p = parts()
    actual = combined(p)
    if actual == BASELINE:
        print(f"sync logic unchanged ({actual[:16]}...)")
        return 0

    print("SYNC LOGIC HAS CHANGED", file=sys.stderr)
    print(f"  baseline: {BASELINE}", file=sys.stderr)
    print(f"  actual:   {actual}", file=sys.stderr)
    print("\n  per-part hashes (compare against a known-good run to find the edit):",
          file=sys.stderr)
    for name, src in p.items():
        print(f"    {hashlib.sha256(src.encode()).hexdigest()[:12]}  {name}", file=sys.stderr)
    print("\n  If this change is intended, update BASELINE in this file in the same"
          "\n  commit. If it is not, revert it -- see the module docstring for what"
          "\n  each property protects.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main_())
