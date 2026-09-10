"""The comparison baselines.

Their job is to be *fair*. v1.0's baseline handed out free points and advanced
almost everything, which made the headline meaningless. These tests pin the
properties that keep the comparison honest: the deterministic baseline uses the
system's own thresholds, records no quotes, and applies no policy.
"""

from __future__ import annotations

import json

import httpx
import pytest

from packet_review_os import llm
from packet_review_os.baseline import (
    baseline_prompt_preview,
    llm_prompt_baseline,
    no_policy_action,
    no_policy_baseline,
)
from packet_review_os.config import app_config
from packet_review_os.evidence import overall_score
from packet_review_os.schemas import NextAction


def test_baseline_records_no_quotes(role, packet_text):
    scores = no_policy_baseline(packet_text("TC01"), role)
    assert scores
    assert all(c.evidence_quote == "" for c in scores)
    assert all(c.evidence_found is False for c in scores)


def test_baseline_gives_no_free_points_for_absent_evidence(role):
    """The v1.0 rig: non-must-have criteria scored 2 whether or not anything matched."""
    scores = no_policy_baseline("Completely unrelated text about gardening.", role)
    assert all(c.score == 0 for c in scores)


def test_baseline_credits_real_indicator_matches(role, packet_text):
    scores = no_policy_baseline(packet_text("TC01"), role)
    python = next(c for c in scores if c.id == "python_backend")
    assert python.score >= 2, "a genuinely strong packet should score well even without policy"


def test_baseline_uses_the_systems_own_thresholds(role, packet_text):
    """A different bar for the baseline would make the comparison meaningless."""
    policy = app_config().action_policy
    scores = no_policy_baseline(packet_text("TC01"), role)
    overall = overall_score(scores)
    expected = (
        NextAction.ADVANCE_TO_SCREEN
        if overall >= policy.advance_min_overall
        else NextAction.HOLD_FOR_SPECIFIC_INTERVIEW
        if overall >= policy.hold_min_overall
        else NextAction.REJECT_WITH_REASON
    )
    assert no_policy_action(scores) is expected


def test_baseline_has_no_knockout_or_referral_policy(role, packet_text):
    """This absence is the hypothesis under test, so it is asserted, not assumed."""
    conduct = no_policy_action(no_policy_baseline(packet_text("TC11"), role))
    assert conduct is NextAction.ADVANCE_TO_SCREEN, (
        "the baseline is expected to miss a conduct knockout; that is the gap the system closes"
    )


def test_baseline_never_asks_back_or_escalates(role, packet_text):
    for case in ["TC01", "TC03", "TC05", "TC11"]:
        action = no_policy_action(no_policy_baseline(packet_text(case), role))
        assert action not in {NextAction.REQUEST_MISSING_INFO, NextAction.ESCALATE_TO_HIRING_MANAGER}


# --------------------------------------------------------------------------- #
# The stronger "simple ChatGPT use" baseline
# --------------------------------------------------------------------------- #


def _chat(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


def test_llm_baseline_parses_a_recommended_action(monkeypatch, role, packet_text, make_settings):
    stub = make_settings(openai_api_key="k")
    monkeypatch.setattr(llm, "settings", lambda: stub)
    monkeypatch.setattr("packet_review_os.baseline.settings", lambda: stub)
    payload = {"recommended_action": "advance_to_screen", "reason": "strong full-stack packet"}
    with llm.stub_transport(lambda _r: _chat(payload)):
        action, reason = llm_prompt_baseline(packet_text("TC01"), role)
    assert action is NextAction.ADVANCE_TO_SCREEN
    assert "strong" in reason


def test_llm_baseline_unparseable_action_counts_as_advance(monkeypatch, role, packet_text, make_settings):
    """Crediting a garbled reply with a safe abstention would flatter the baseline."""
    stub = make_settings(openai_api_key="k")
    monkeypatch.setattr(llm, "settings", lambda: stub)
    monkeypatch.setattr("packet_review_os.baseline.settings", lambda: stub)
    with llm.stub_transport(lambda _r: _chat({"recommended_action": "hire them immediately"})):
        action, _ = llm_prompt_baseline(packet_text("TC01"), role)
    assert action is NextAction.ADVANCE_TO_SCREEN


def test_llm_baseline_without_a_key_is_reported_as_not_run(monkeypatch, role, packet_text, make_settings):
    monkeypatch.setattr("packet_review_os.baseline.settings", lambda: make_settings(openai_api_key=""))
    with pytest.raises(llm.LLMError, match="not run"):
        llm_prompt_baseline(packet_text("TC01"), role)


def test_baseline_prompt_is_published_for_the_appendix(role, packet_text):
    """Reviewers should be able to read the exact prompt the baseline was given."""
    preview = baseline_prompt_preview(packet_text("TC01"), role)
    assert "recommended_action" in preview
    # The baseline deliberately receives no per-criterion scorecard and no
    # grounding rules -- that absence is what it is a baseline for.
    assert "must_have" not in preview
    assert "evidence_quote" not in preview
    assert "verbatim" not in preview.lower()
