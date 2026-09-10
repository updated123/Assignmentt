"""The model is a proposer, not the decider.

These tests pin the guarantees documented in ``llm.merge_llm_criteria`` and the
retry/fallback classification in ``llm.complete_json``. They run against a stub
transport, so no API key and no network are involved.
"""

from __future__ import annotations

import json

import httpx
import pytest

from packet_review_os import llm
from packet_review_os.evidence import score_criteria
from packet_review_os.schemas import CriterionScore

PACKET = (
    "Priya Nair. 5 years of experience as a software engineer. "
    "Shipped Python FastAPI services and a React/TypeScript dashboard for a B2B SaaS billing product."
)


def _chat(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


def _criterion(**kwargs) -> CriterionScore:
    base = {"id": "python_backend", "label": "Production Python", "score": 2, "must_have": True}
    base.update(kwargs)
    return CriterionScore(**base)


# --------------------------------------------------------------------------- #
# Grounding enforcement
# --------------------------------------------------------------------------- #


def test_hallucinated_quote_is_dropped_and_score_pulled_back(role):
    extractive = [_criterion(score=2, evidence_quote="Shipped Python FastAPI services")]
    warnings: list[str] = []
    merged = llm.merge_llm_criteria(
        PACKET,
        role,
        extractive,
        {
            "criteria": [
                {
                    "id": "python_backend",
                    "score": 3,
                    "evidence_quote": "Led the Kubernetes platform team at Google for four years.",
                }
            ]
        },
        warnings,
    )
    crit = merged[0]
    assert crit.evidence_quote == "Shipped Python FastAPI services"
    assert crit.score < 3, "a score that depended on an invented quote must not stand"
    assert any("hallucinated" in w.lower() for w in warnings)


def test_high_score_without_any_quote_is_capped(role):
    warnings: list[str] = []
    merged = llm.merge_llm_criteria(
        PACKET,
        role,
        [_criterion(score=0, evidence_quote="")],
        {"criteria": [{"id": "python_backend", "score": 3, "evidence_quote": ""}]},
        warnings,
    )
    assert merged[0].score <= 1
    assert any("no grounded quote" in w for w in warnings)


def test_grounded_quote_is_accepted(role):
    warnings: list[str] = []
    merged = llm.merge_llm_criteria(
        PACKET,
        role,
        [_criterion(score=1, evidence_quote="")],
        {
            "criteria": [
                {
                    "id": "python_backend",
                    "score": 3,
                    "evidence_quote": "Shipped Python FastAPI services",
                    "rationale": "Quoted verbatim.",
                }
            ]
        },
        warnings,
    )
    assert merged[0].score == 3
    assert merged[0].evidence_found is True
    assert warnings == []


def test_unknown_criterion_id_is_ignored_not_inserted(role):
    warnings: list[str] = []
    merged = llm.merge_llm_criteria(
        PACKET,
        role,
        [_criterion()],
        {"criteria": [{"id": "invented_criterion", "score": 3, "evidence_quote": "x"}]},
        warnings,
    )
    assert [c.id for c in merged] == ["python_backend"]


def test_malformed_payload_leaves_extractive_scores_untouched(role):
    extractive = score_criteria(PACKET, role)
    warnings: list[str] = []
    merged = llm.merge_llm_criteria(PACKET, role, extractive, {"criteria": "not a list"}, warnings)
    assert [c.score for c in merged] == [c.score for c in extractive]
    assert warnings


def test_non_numeric_score_falls_back_to_extractive(role):
    warnings: list[str] = []
    merged = llm.merge_llm_criteria(
        PACKET,
        role,
        [_criterion(score=2, evidence_quote="Shipped Python FastAPI services")],
        {"criteria": [{"id": "python_backend", "score": "excellent"}]},
        warnings,
    )
    assert merged[0].score == 2
    assert any("Non-numeric" in w for w in warnings)


def test_contrary_evidence_must_also_be_grounded(role):
    warnings: list[str] = []
    merged = llm.merge_llm_criteria(
        PACKET,
        role,
        [_criterion()],
        {
            "criteria": [
                {
                    "id": "python_backend",
                    "score": 2,
                    "evidence_quote": "Shipped Python FastAPI services",
                    "contrary_evidence": "Candidate admitted they have never written Python.",
                }
            ]
        },
        warnings,
    )
    assert merged[0].contrary_evidence != "Candidate admitted they have never written Python."


# --------------------------------------------------------------------------- #
# Transport: retryable vs fatal
# --------------------------------------------------------------------------- #


def test_valid_response_is_parsed(make_settings):
    cfg = make_settings(openai_api_key="k")
    with llm.stub_transport(lambda _r: _chat({"summary": "ok"})):
        assert llm.complete_json("s", "u", cfg) == {"summary": "ok"}


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_codes_are_retryable(make_settings, status):
    cfg = make_settings(openai_api_key="k")
    with llm.stub_transport(lambda _r: httpx.Response(status, text="later")):
        with pytest.raises(llm.LLMError) as exc:
            llm.complete_json("s", "u", cfg)
        assert not isinstance(exc.value, llm.LLMFatalError)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_fatal_and_not_retried(make_settings, status):
    cfg = make_settings(openai_api_key="k")
    with llm.stub_transport(lambda _r: httpx.Response(status, text="bad key")), pytest.raises(llm.LLMFatalError):
        llm.complete_json("s", "u", cfg)


def test_timeout_is_reported_as_retryable(make_settings):
    cfg = make_settings(openai_api_key="k", llm_timeout_seconds=1.0)

    def timeout(_request):
        raise httpx.ConnectTimeout("stub")

    with llm.stub_transport(timeout), pytest.raises(llm.LLMError, match="timed out"):
        llm.complete_json("s", "u", cfg)


def test_non_json_content_is_rejected(make_settings):
    cfg = make_settings(openai_api_key="k")
    body = {"choices": [{"message": {"content": "here you go: not json"}}]}
    with llm.stub_transport(lambda _r: httpx.Response(200, json=body)):
        with pytest.raises(llm.LLMError, match="invalid JSON"):
            llm.complete_json("s", "u", cfg)


def test_json_array_is_rejected(make_settings):
    cfg = make_settings(openai_api_key="k")
    body = {"choices": [{"message": {"content": "[1, 2, 3]"}}]}
    with llm.stub_transport(lambda _r: httpx.Response(200, json=body)):
        with pytest.raises(llm.LLMError, match="JSON object"):
            llm.complete_json("s", "u", cfg)


def test_unexpected_envelope_is_rejected(make_settings):
    cfg = make_settings(openai_api_key="k")
    with llm.stub_transport(lambda _r: httpx.Response(200, json={"unexpected": True})):
        with pytest.raises(llm.LLMError, match="envelope"):
            llm.complete_json("s", "u", cfg)


def test_fatal_error_short_circuits_the_retry_loop(monkeypatch, role, make_settings):
    """A bad key must not be retried; retrying cannot fix it."""
    cfg = make_settings(openai_api_key="k", llm_retries=3)
    monkeypatch.setattr(llm, "settings", lambda: cfg)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(401, text="invalid api key")

    with llm.stub_transport(handler), pytest.raises(llm.LLMFatalError):
        llm.judge_with_llm(PACKET, role, [])
    assert calls["n"] == 1


def test_retryable_error_uses_every_configured_attempt(monkeypatch, role, make_settings):
    cfg = make_settings(openai_api_key="k", llm_retries=2)
    monkeypatch.setattr(llm, "settings", lambda: cfg)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    with llm.stub_transport(handler), pytest.raises(llm.LLMError):
        llm.judge_with_llm(PACKET, role, [])
    assert calls["n"] == 3, "retries=2 means three attempts in total"


def test_judge_without_key_is_fatal(monkeypatch, role, make_settings):
    monkeypatch.setattr(llm, "settings", lambda: make_settings(openai_api_key=""))
    with pytest.raises(llm.LLMFatalError):
        llm.judge_with_llm(PACKET, role, [])


def test_prompt_carries_the_grounding_rules_and_truncates_long_packets(role):
    system, user = llm.build_judge_messages("x" * 40_000, role, [])
    assert "verbatim" in user.lower()
    assert "cheerleader" in system.lower()
    assert len(user) < 40_000, "packet must be truncated before it reaches the model"
