"""Optional LLM judge.

The model is a *proposer*, never the decider. It may adjust criterion scores and
supply quotes; it cannot choose the next action, because the action policy lives
in :mod:`policy` where it can be read, tested and argued with. Anything the model
returns passes through :func:`merge_llm_criteria`, which drops quotes that are not
verbatim substrings of the packet and refuses to let an unquoted claim score
above 1.

If the model is unavailable, slow, or returns unusable JSON, the pipeline falls
back to the grounded extractive engine and says so in the result
(``engine_mode = llm_fallback_extractive``). A review is never blocked on the
model being up.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from .config import RoleConfig, Settings, settings
from .schemas import CriterionScore, NextAction
from .textutil import quote_in_source

logger = logging.getLogger("packet_review_os.llm")

MAX_PACKET_CHARS_TO_MODEL = 14_000
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Test seam. The evaluation and the unit tests exercise the real request/parse/
# retry/fallback code against a stub transport, so the model path is covered
# without an API key and without network access. Production leaves this at None,
# which makes httpx use its default transport.
_TEST_TRANSPORT: httpx.BaseTransport | None = None


def set_test_transport(transport: httpx.BaseTransport | None) -> None:
    """Install (or clear) a stub HTTP transport for the model calls."""
    global _TEST_TRANSPORT
    _TEST_TRANSPORT = transport


@contextmanager
def stub_transport(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[None]:
    """Route model calls to ``handler`` for the duration of the block."""
    previous = _TEST_TRANSPORT
    set_test_transport(httpx.MockTransport(handler))
    try:
        yield
    finally:
        set_test_transport(previous)


class LLMError(RuntimeError):
    """Model call failed. Always recoverable: the caller falls back to extractive."""


class LLMFatalError(LLMError):
    """Model call failed in a way retrying cannot fix (bad key, bad request)."""


def llm_enabled() -> bool:
    return settings().llm_enabled


def _schema_hint() -> str:
    return json.dumps(
        {
            "summary": "2-3 sentences, only facts from the packet",
            "criteria": [
                {
                    "id": "years_experience",
                    "score": 0,
                    "evidence_quote": "verbatim substring of the packet or empty",
                    "rationale": "one sentence",
                    "contrary_evidence": "",
                }
            ],
            "knockout_ids": [],
            "risk_flag_ids": [],
            "recommended_action": "advance_to_screen",
            "why_this_action": "one sentence",
            "interview_probes": ["question"],
            "warnings": [],
        },
        indent=2,
    )


def build_judge_messages(
    packet: str,
    role: RoleConfig,
    extractive_criteria: list[CriterionScore],
) -> tuple[str, str]:
    """Build the (system, user) pair for the judge call. Pure, so it is testable."""
    by_id = {c.id: c for c in extractive_criteria}
    criteria_brief = [
        {
            "id": c.id,
            "label": c.label,
            "must_have": c.must_have,
            "extractive_score": by_id[c.id].score if c.id in by_id else None,
            "extractive_quote": by_id[c.id].evidence_quote if c.id in by_id else "",
        }
        for c in role.criteria
    ]
    payload = {
        "role": {"id": role.role_id, "title": role.title, "summary": role.summary},
        "scorecard": criteria_brief,
        "knockouts": [{"id": k.id, "label": k.label} for k in role.knockouts],
        "risk_flags": [{"id": r.id, "label": r.label} for r in role.risk_flags],
        "allowed_actions": [a.value for a in NextAction],
        "packet": packet[:MAX_PACKET_CHARS_TO_MODEL],
        "rules": [
            "Quotes MUST be verbatim substrings of the packet. If you cannot quote, "
            "leave evidence_quote empty and score 0 or 1.",
            "Do not invent employers, dates, or skills.",
            "Must-have with no evidence cannot be scored 3.",
            "recommended_action is advisory; a local policy may override it.",
        ],
    }
    system = (
        "You are a hiring-scorecard judge, not a cheerleader. "
        "Return JSON only. Prefer precision over generosity."
    )
    user = (
        "Score this candidate packet against the scorecard. "
        f"JSON shape:\n{_schema_hint()}\n\nINPUT:\n{json.dumps(payload)}"
    )
    return system, user


def judge_with_llm(
    packet: str,
    role: RoleConfig,
    extractive_criteria: list[CriterionScore],
) -> dict[str, Any]:
    """Call the judge with bounded retries. Raises LLMError on final failure."""
    cfg = settings()
    if not cfg.llm_enabled:
        raise LLMFatalError("OPENAI_API_KEY is not set.")
    system, user = build_judge_messages(packet, role, extractive_criteria)

    attempts = max(1, cfg.llm_retries + 1)
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            return complete_json(system, user, cfg)
        except LLMFatalError:
            raise
        except LLMError as exc:
            last_err = exc
            if attempt + 1 < attempts:
                backoff = 0.6 * (2**attempt)
                logger.warning(
                    "LLM judge attempt %d/%d failed (%s). Retrying in %.1fs.",
                    attempt + 1,
                    attempts,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
    raise LLMError(f"LLM judge failed after {attempts} attempt(s): {last_err}")


def complete_json(system: str, user: str, cfg: Settings | None = None) -> dict[str, Any]:
    """POST a chat completion and parse a JSON object out of it.

    Distinguishes retryable transport/rate-limit failures (LLMError) from fatal
    request failures such as a bad key or a rejected body (LLMFatalError), so
    retries are not wasted on errors that cannot succeed.
    """
    cfg = cfg or settings()
    url = cfg.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.openai_api_key}", "Content-Type": "application/json"}
    body = {
        "model": cfg.openai_model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    logger.info("Calling %s model=%s prompt_chars=%d", url, cfg.openai_model, len(system) + len(user))
    try:
        with httpx.Client(timeout=cfg.llm_timeout_seconds, transport=_TEST_TRANSPORT) as client:
            response = client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise LLMError(f"LLM request timed out after {cfg.llm_timeout_seconds:g}s.") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:300]
        if response.status_code in RETRYABLE_STATUS:
            raise LLMError(f"LLM HTTP {response.status_code} (retryable): {detail}")
        raise LLMFatalError(f"LLM HTTP {response.status_code}: {detail}")

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM response envelope was not usable: {exc}") from exc
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMError("LLM returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise LLMError("LLM did not return a JSON object.")
    return parsed


def merge_llm_criteria(
    packet: str,
    role: RoleConfig,
    extractive: list[CriterionScore],
    llm_payload: dict[str, Any],
    warnings: list[str],
) -> list[CriterionScore]:
    """Merge model scores over extractive ones, enforcing the grounding rules.

    Guarantees, each of which has a regression test in tests/test_llm_merge.py:

    * a quote that is not a verbatim substring of the packet is dropped, and a
      score of 3 that relied on it is pulled back down;
    * any score of 2 or more without a grounded quote is capped at 1;
    * unknown criterion ids are ignored rather than inserted;
    * a malformed payload leaves the extractive scores untouched.
    """
    known_ids = {c.id for c in role.criteria}
    by_id = {c.id: c.model_copy(deep=True) for c in extractive}
    raw_list = llm_payload.get("criteria")
    if not isinstance(raw_list, list):
        warnings.append("LLM criteria payload was not a list; kept extractive scores.")
        return extractive

    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id") or "")
        if cid not in by_id or cid not in known_ids:
            if cid:
                warnings.append(f"Ignored unknown criterion id from model: {cid!r}.")
            continue
        current = by_id[cid]
        try:
            score = int(raw.get("score", current.score))
        except (TypeError, ValueError):
            warnings.append(f"Non-numeric score from model on {cid}; kept extractive score.")
            score = current.score
        score = min(3, max(0, score))

        quote = str(raw.get("evidence_quote") or "").strip()
        if quote and not quote_in_source(quote, packet):
            warnings.append(f"Dropped hallucinated quote on {cid}.")
            quote = ""
            if score >= 3:
                score = max(current.score, 1)
        if not quote:
            # The model supplied no usable quote. Fall back to the extractive
            # one rather than discarding evidence the packet really contains --
            # otherwise a partial model reply erases grounding and drags the
            # score down with it.
            quote = current.evidence_quote if quote_in_source(current.evidence_quote, packet) else ""
        if score >= 2 and not quote:
            warnings.append(f"Lowered {cid}: score {score} had no grounded quote.")
            score = min(score, 1)

        current.score = score
        current.evidence_quote = quote
        current.evidence_found = bool(quote)
        current.rationale = str(raw.get("rationale") or current.rationale)[:400]
        contrary = str(raw.get("contrary_evidence") or "")
        if contrary and quote_in_source(contrary, packet):
            current.contrary_evidence = contrary
        by_id[cid] = current
    return list(by_id.values())
