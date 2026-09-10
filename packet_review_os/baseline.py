"""Comparison baselines for the evaluation. Never used in the product path.

The point of a baseline is to isolate what the system actually adds. This system
adds two things on top of "read the packet and form a view": **grounding** (every
score points at a quotable span) and **policy** (must-have gates, knockouts,
referral escalation, ask-back instead of guessing). So the baseline must be a
credible reviewer that lacks exactly those two things -- not a deliberately bad
one.

v1.0 got this wrong. Its baseline handed out a free score of 2 to non-must-have
criteria regardless of evidence, advanced anything scoring 0.4 or above, and the
eval harness then hard-coded its grounding check to ``False``. It scored 1/12,
which proved nothing except that a rigged opponent loses. Two changes here:

* :func:`no_policy_baseline` scores from the same indicator lists as the real
  scorer, with no generosity hack. It is a fair reviewer.
* :func:`no_policy_action` decides on the weighted score alone, using the *same*
  thresholds as the system. It has no knockouts, no referral rule, no must-have
  gate and no ask-back -- which is the whole hypothesis under test.

:func:`llm_prompt_baseline` is the stronger comparison the brief asks for -- a
single unstructured "should we advance this candidate?" call to a real model. It
requires an API key, so the reported headline uses the deterministic baseline
and names it as such. See docs/EVALUATION.md.
"""

from __future__ import annotations

import json
import logging

from .config import RoleConfig, app_config, settings
from .schemas import CriterionScore, NextAction

logger = logging.getLogger("packet_review_os.baseline")


def no_policy_baseline(packet: str, role: RoleConfig) -> list[CriterionScore]:
    """Score criteria by indicator presence, without grounding a quote.

    Same evidence signal as the real scorer, but it records no quote and does not
    consult negative indicators -- the failure mode of reading a packet quickly
    and remembering only what was present, never what was explicitly absent.
    """
    blob = packet.lower()
    results: list[CriterionScore] = []
    for criterion in role.criteria:
        hits = sum(1 for term in criterion.indicators if term.lower() in blob)
        if hits >= 2:
            score = 3
        elif hits == 1:
            score = 2
        else:
            score = 0
        results.append(
            CriterionScore(
                id=criterion.id,
                label=criterion.label,
                score=score,
                must_have=criterion.must_have,
                weight=criterion.weight,
                evidence_quote="",
                evidence_found=False,
                rationale=f"Baseline matched {hits} indicator(s) without requiring a verbatim quote.",
            )
        )
    return results


def no_policy_action(criteria: list[CriterionScore]) -> NextAction:
    """Decide from the weighted score alone, at the system's own thresholds.

    No knockout check, no referral escalation, no must-have gate, no ask-back.
    """
    from .evidence import overall_score

    policy = app_config().action_policy
    overall = overall_score(criteria)
    if overall >= policy.advance_min_overall:
        return NextAction.ADVANCE_TO_SCREEN
    if overall >= policy.hold_min_overall:
        return NextAction.HOLD_FOR_SPECIFIC_INTERVIEW
    return NextAction.REJECT_WITH_REASON


BASELINE_PROMPT = (
    "You are helping a startup hiring manager triage a candidate packet.\n"
    "Read the role and the packet, then answer with JSON: "
    '{{"recommended_action": one of ["advance_to_screen", "hold_for_specific_interview", '
    '"request_missing_info", "reject_with_reason", "escalate_to_hiring_manager"], '
    '"reason": "one sentence"}}\n\n'
    "ROLE: {title}\n{summary}\n\nPACKET:\n{packet}\n"
)


def llm_prompt_baseline(packet: str, role: RoleConfig) -> tuple[NextAction, str]:
    """A single unstructured prompt -- 'simple ChatGPT use' -- with no scorecard.

    This is the strongest honest baseline: a capable model, no grounding
    requirement and no policy layer. Raises LLMError when no key is configured,
    so the evaluation reports it as "not run" rather than silently substituting
    the deterministic baseline.
    """
    from .llm import LLMError, complete_json

    cfg = settings()
    if not cfg.llm_enabled:
        raise LLMError("LLM baseline needs OPENAI_API_KEY. Reported as not run.")
    prompt = BASELINE_PROMPT.format(
        title=role.title or role.role_id,
        summary=role.summary or "",
        packet=packet[:14000],
    )
    payload = complete_json(
        system="You are a helpful hiring assistant. Return JSON only.",
        user=prompt,
        cfg=cfg,
    )
    raw = str(payload.get("recommended_action") or "").strip()
    reason = str(payload.get("reason") or "")[:400]
    try:
        return NextAction(raw), reason
    except ValueError:
        logger.warning("LLM baseline returned unknown action %r; recording as advance.", raw)
        # A naive caller would most likely read an unparseable positive answer as
        # "yes, move forward". Recording it as advance keeps the baseline honest
        # rather than crediting it with a safe abstention it did not make.
        return NextAction.ADVANCE_TO_SCREEN, reason or f"unparsed action {raw!r}"


def baseline_prompt_preview(packet: str, role: RoleConfig) -> str:
    """Exact prompt text used by the LLM baseline, for the evaluation appendix."""
    return json.dumps(
        {
            "system": "You are a helpful hiring assistant. Return JSON only.",
            "user": BASELINE_PROMPT.format(
                title=role.title or role.role_id,
                summary=role.summary or "",
                packet=packet[:400] + ("..." if len(packet) > 400 else ""),
            ),
        },
        indent=2,
    )
