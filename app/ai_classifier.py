"""An LLM that proposes a cause for an unmatched row.

This exists to be measured, not assumed. `app/classify.py` already assigns a
cause deterministically, and for most of what reconciliation runs into that is
the right tool: deciding whether an amount delta exceeds a tolerance, or
whether a timestamp falls inside a window, is arithmetic against a constant.
A language model asked to compare 101 against 100 is being misused, and the
stress evaluation shows the rules winning those cases outright.

Where the rules structurally cannot help is when the evidence is *language*.
The matcher's counterparty fallback is exact string equality
(reconcile.py:111), so a payee written with its legal suffix is a different
payee; a reference id transcribed with a letter O for a zero is a different
payment; a bank's own service-charge line is an unexplained orphan. All three
are obvious to anyone who reads the row, and unreachable by any tolerance
constant. That is the gap this fills.

Three hard limits, none of them optional:

  It never selects an action. classify.py's cause feeds router.ACTION_MAP,
  which is what decides whether money moves; this returns a *proposal* that a
  human or the rules dispose of. The LLM is nowhere in a payout path.

  It abstains rather than guesses. Below AI_CLASSIFIER_MIN_CONFIDENCE the
  answer is discarded and the row is left to the rules. A confident wrong
  cause is worse than no cause, because the cause is what picks the action.

  It fails to the rules. Any transport error, timeout, malformed reply or
  unknown cause returns the rule's answer with the failure recorded. The
  pipeline is never *worse* off for the model being unreachable -- which is
  not hypothetical: the gateway this is currently pointed at rejects every
  request, and the evaluation still runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from typing import Callable, NamedTuple

from app import anthropic_client, config, gemini_client
from app.classify import CAUSES


class Provider(NamedTuple):
    """What the classifier needs from whichever model it is asking.

    Named rather than a bare tuple because the call sites were already reading
    it positionally -- `_provider()[3]` for the model name, a four-way unpack in
    propose() -- so adding a field (a per-provider timeout, say) meant fixing
    every unpack site and hoping the order stayed right, with nothing to catch
    it if it did not.
    """

    is_configured: Callable[[], bool]
    call: Callable[[str, str], str]
    error: type[Exception]
    model: str


def _provider() -> Provider:
    """The configured provider's (is_configured, call) pair.

    A seam rather than a hard import because this project has now run the
    classifier against two providers in one week: the AgentRouter gateway
    rejects every request pre-auth, so the measured runs are on Gemini. Which
    model produced a number is part of the number, so the provider is named in
    config rather than inferred from whichever key happens to be present -- two
    are, and picking by presence would make a published result depend on
    environment ordering.
    """
    if config.AI_CLASSIFIER_PROVIDER == "anthropic":
        return Provider(
            is_configured=anthropic_client.is_configured,
            call=lambda system, prompt: anthropic_client.complete(
                system=system,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
            ),
            error=anthropic_client.AnthropicError,
            model=config.ANTHROPIC_MODEL,
        )
    return Provider(
        is_configured=gemini_client.is_configured,
        call=lambda system, prompt: gemini_client.generate(
            system=system, prompt=prompt, max_output_tokens=2048,
        ),
        error=gemini_client.GeminiError,
        model=config.GEMINI_MODEL,
    )


def active_model() -> str:
    """The model behind the current provider, for reports and the audit log."""
    return _provider().model

#: Returned when the model declines, or when its answer is unusable. Scored
#: separately from a wrong answer by app/evaluation.py -- declining to label a
#: row is not the same failure as labelling it wrongly, and a metric that
#: conflated them would punish exactly the caution we asked for.
ABSTAIN = "abstain"

_SYSTEM = """You classify unreconciled payment records for an Indian payouts \
reconciliation system.

You are given one record that a deterministic matcher could not reconcile, \
plus the cause its rules assigned. Those rules compare reference ids, amounts \
and timestamps. They cannot read text. Your value is only in evidence they \
cannot see: the narration, the counterparty name, and how identifiers are \
written.

Valid causes:
- failed_payment: the payout never reached the gateway or bank at all.
- fee_mismatch: it settled, but for a different amount, because a fee was taken.
- timing_lag: it settled correctly but outside the expected window.
- duplicate: the same payment appears more than once.
- refund_unmatched: a refund with no matching original.
- chargeback: a chargeback.
- partial_payment: a split or batch that does not sum to the expected total.
- unexplained: it correlates to nothing.
- matched: NOT a discrepancy. The two sides are in fact the same payment and \
the matcher missed it -- e.g. the payee is written differently on each side, or \
a reference id was transcribed wrongly (a letter O for a zero, l for 1).
- bank_charge: not a payout at all. The bank's own fee, identifiable from its \
narration.

Reply with only a JSON object, no prose and no code fence:
{"cause": "<one of the causes above>", "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}

Set confidence below 0.6 when the text gives you no more than the rules already \
had. Do not restate the rule's answer with high confidence merely because it is \
plausible -- if you have no textual evidence, say so with a low number."""

#: Causes the model may return that classify.py has no name for. They are the
#: point of asking it: "matched" and "bank_charge" are exactly the two findings
#: the rules cannot reach.
EXTRA_CAUSES = ("matched", "bank_charge")
_VALID = frozenset(CAUSES) | frozenset(EXTRA_CAUSES)


@dataclass
class Proposal:
    cause: str
    confidence: float
    reasoning: str
    #: True when this came from the fallback path rather than the model.
    fell_back: bool = False
    #: Populated when the model was unreachable or unusable, for the audit log.
    error: str = ""


def _record_block(row: dict, rule_cause: str) -> str:
    """The row as the model sees it.

    Deliberately includes narration, counterparty and raw_json -- the fields
    classify.py never reads -- because those are the entire reason for asking.
    Sending only what the rules use would guarantee it could do no better.
    """
    fields = {
        "rule_assigned_cause": rule_cause,
        "ledger_reference": row.get("ledger_reference"),
        "gateway_reference": row.get("gateway_reference"),
        "amount_rupees": (row.get("amount_paise") or 0) / 100,
        "counterparty_ledger": row.get("counterparty_ledger"),
        "counterparty_other": row.get("counterparty_other"),
        "narration": row.get("narration"),
        "hours_apart": row.get("hours_apart"),
        "amount_delta_rupees": row.get("amount_delta_paise") and row["amount_delta_paise"] / 100,
        "detail": row.get("detail"),
    }
    present = {k: v for k, v in fields.items() if v not in (None, "")}
    return json.dumps(present, indent=2, default=str)


def _parse(text: str) -> tuple[str, float, str]:
    """Pull the JSON object out of the reply.

    Tolerant of a code fence or a stray sentence around it, because that is a
    normal thing for a model to add and refusing the whole answer over
    punctuation would throw away a correct classification. Not tolerant of
    anything past that: an unparseable reply abstains rather than guesses.
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in reply")
    data = json.loads(match.group(0))
    cause = str(data.get("cause", "")).strip().lower()
    if cause not in _VALID:
        raise ValueError(f"unknown cause {cause!r}")
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return cause, max(0.0, min(1.0, confidence)), str(data.get("reasoning", "")).strip()


def propose(row: dict, rule_cause: str) -> Proposal:
    """One classification. Never raises: every failure path returns a Proposal.

    The caller is a batch evaluation and, later, a per-row advisory endpoint.
    Neither has anything useful to do with an exception from a third-party API,
    and both have something useful to do with "the rules' answer, plus a note
    saying why there is no second opinion".
    """
    provider = _provider()

    if not provider.is_configured():
        return Proposal(cause=rule_cause, confidence=0.0, reasoning="", fell_back=True,
                        error=f"no API key for provider {config.AI_CLASSIFIER_PROVIDER!r}")

    try:
        reply = provider.call(_SYSTEM, _record_block(row, rule_cause))
    except provider.error as exc:
        # The upstream is unreachable or refusing us. Hand back the rules'
        # answer so the pipeline is exactly as good as it was before the model
        # existed, and record why for the audit trail.
        return Proposal(cause=rule_cause, confidence=0.0, reasoning="",
                        fell_back=True, error=f"{type(exc).__name__}: {exc.status_code}")
    except Exception as exc:  # httpx transport errors, DNS, timeouts
        return Proposal(cause=rule_cause, confidence=0.0, reasoning="",
                        fell_back=True, error=f"{type(exc).__name__}")

    try:
        cause, confidence, reasoning = _parse(reply)
    except (ValueError, json.JSONDecodeError) as exc:
        return Proposal(cause=rule_cause, confidence=0.0, reasoning="",
                        fell_back=True, error=f"unparseable reply: {exc}")

    if confidence < config.AI_CLASSIFIER_MIN_CONFIDENCE:
        # It answered, but not confidently enough to act on. Keep the reasoning
        # -- an operator reading the audit trail wants to know what it was
        # unsure about, not merely that it was.
        return Proposal(cause=ABSTAIN, confidence=confidence, reasoning=reasoning)

    return Proposal(cause=cause, confidence=confidence, reasoning=reasoning)
