"""The AI classifier's contract: it may be wrong, but it must never be loud
about being unavailable, and it must never leave the pipeline worse off than
the rules alone.

Every test here monkeypatches the transport. None of them touch the network --
a test suite that needed a third-party API key to pass would be a test suite
nobody could run, and the whole point of the fallback path is that it works
when that key does not.
"""

from __future__ import annotations

import pytest

from app import config, gemini_client
from app.ai_classifier import ABSTAIN, propose
from app.gemini_client import GeminiError

ROW = {
    "ledger_reference": "STR-0021",
    "gateway_reference": "pout_stress0021",
    "amount_paise": 412_300,
    "counterparty_ledger": "Meridian Textiles",
    "counterparty_other": "Meridian Textiles Pvt Ltd",
    "narration": "NEFT DR Meridian Textiles Pvt Ltd",
}


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Pretend a key exists, and pin the provider so these never depend on
    whatever .env happens to select. Tests about an absent key clear it."""
    monkeypatch.setattr(config, "AI_CLASSIFIER_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key-not-real")


def _reply(monkeypatch, text: str):
    # Patched on the client module, not on ai_classifier: propose() resolves
    # its provider through _provider() at call time, so there is no name bound
    # in ai_classifier to replace.
    monkeypatch.setattr(gemini_client, "generate", lambda **_: text)


def _raises(monkeypatch, exc: Exception):
    def boom(**_):
        raise exc
    monkeypatch.setattr(gemini_client, "generate", boom)


# --- the useful answer ------------------------------------------------------

def test_reads_a_cause_the_rules_cannot_reach(monkeypatch):
    # "matched" is the whole reason for asking a language model: the rules
    # compared two counterparty strings for equality and concluded these were
    # different payees. Reading them says otherwise.
    _reply(monkeypatch, '{"cause": "matched", "confidence": 0.94, '
                        '"reasoning": "Same payee with a legal suffix."}')
    p = propose(ROW, rule_cause="failed_payment")
    assert p.cause == "matched"
    assert p.confidence == pytest.approx(0.94)
    assert not p.fell_back


def test_tolerates_a_code_fence_around_the_json(monkeypatch):
    # Models add fences. Throwing away a correct classification over
    # punctuation would be a silly way to lose accuracy.
    _reply(monkeypatch, '```json\n{"cause": "bank_charge", "confidence": 0.9, '
                        '"reasoning": "Narration names the bank\'s own fee."}\n```')
    assert propose(ROW, rule_cause="unexplained").cause == "bank_charge"


# --- abstention -------------------------------------------------------------

def test_abstains_below_the_confidence_floor(monkeypatch):
    _reply(monkeypatch, '{"cause": "matched", "confidence": 0.3, "reasoning": "Not sure."}')
    p = propose(ROW, rule_cause="failed_payment")
    assert p.cause == ABSTAIN
    assert not p.fell_back, "an abstention is an answer, not a transport failure"


def test_an_abstention_keeps_its_reasoning(monkeypatch):
    # An operator reading the audit trail wants to know what it was unsure
    # about, not just that it was unsure.
    _reply(monkeypatch, '{"cause": "matched", "confidence": 0.2, '
                        '"reasoning": "Names differ but nothing corroborates."}')
    assert "corroborates" in propose(ROW, rule_cause="failed_payment").reasoning


# --- never worse than the rules --------------------------------------------

@pytest.mark.parametrize("failure", [
    GeminiError(401, {"error": "unauthorized"}),
    GeminiError(429, {"error": "quota exhausted"}),
    GeminiError(502, {"error": "model returned no text"}),
    TimeoutError("read timeout"),
    ConnectionError("dns failure"),
])
def test_any_upstream_failure_returns_the_rules_answer(monkeypatch, failure):
    # The 401 case is not hypothetical: the gateway this is pointed at rejects
    # every request pre-auth, and the evaluation still has to run.
    _raises(monkeypatch, failure)
    p = propose(ROW, rule_cause="failed_payment")
    assert p.cause == "failed_payment"
    assert p.fell_back
    assert p.error, "the reason has to survive for the audit trail"


def test_unparseable_reply_falls_back_rather_than_guessing(monkeypatch):
    _reply(monkeypatch, "I think this one is probably a duplicate, honestly.")
    p = propose(ROW, rule_cause="duplicate")
    assert p.fell_back and "unparseable" in p.error


def test_an_invented_cause_is_refused(monkeypatch):
    # A cause outside the known set would flow into router.ACTION_MAP and find
    # no action. Rejecting it here keeps that impossible.
    _reply(monkeypatch, '{"cause": "vibes", "confidence": 0.99, "reasoning": "trust me"}')
    p = propose(ROW, rule_cause="unexplained")
    assert p.cause == "unexplained"
    assert p.fell_back


def test_missing_key_is_not_an_error(monkeypatch):
    # A reviewer cloning the public repo has no key. That must degrade to the
    # rules silently, not raise.
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    p = propose(ROW, rule_cause="timing_lag")
    assert p.cause == "timing_lag"
    assert p.fell_back and "no API key" in p.error


def test_the_model_is_never_asked_to_choose_an_action(monkeypatch):
    # The guarantee that keeps this out of every money path: the classifier's
    # vocabulary is causes, and it has no way to name an action even if it
    # tried. router.ACTION_MAP is the only thing that turns a cause into one.
    from app.router import ACTION_MAP
    _reply(monkeypatch, '{"cause": "retry_payout", "confidence": 1.0, "reasoning": "do it"}')
    p = propose(ROW, rule_cause="failed_payment")
    assert p.cause not in set(ACTION_MAP.values())
    assert p.fell_back
