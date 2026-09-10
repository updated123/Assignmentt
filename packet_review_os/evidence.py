"""Extractive scoring: every score must point at a span of the packet.

This is the grounding layer. A criterion can only earn points if a matching span
of the candidate's own text can be quoted back, which is what makes the review
defensible when the founder asks "why?". The LLM judge in :mod:`llm` may adjust
these scores, but it cannot introduce a quote that is not a substring of the
packet -- see :func:`llm.merge_llm_criteria`.
"""

from __future__ import annotations

from .config import Criterion, RoleConfig
from .schemas import CriterionScore, KnockoutHit, NextAction, RiskFlag
from .textutil import first_matching_window, years_mentioned

NEGATION_TOKENS = (" no ", "not ", "never ", "only", "backend only", "looking for first")

MIN_YEARS = 3.0


def _looks_negative(snippet: str) -> bool:
    text = (snippet or "").lower()
    return any(token in text for token in NEGATION_TOKENS)


def _score_years(years: float | None, positive: str) -> tuple[int, str]:
    if years is None:
        if positive and not _looks_negative(positive):
            return 1, "Engineering work is mentioned but years of experience are not explicit."
        return 0, "Could not find a years-of-experience signal."
    if years >= MIN_YEARS:
        return 3, f"Packet states about {years:g} years of experience."
    if years >= 2:
        return 1, f"Only about {years:g} years found; below the {MIN_YEARS:g}-year must-have."
    return 0, f"Only about {years:g} years found; below the {MIN_YEARS:g}-year must-have."


def _score_from_hits(
    positive: str,
    negative: str,
    must_have: bool,
    years: float | None,
    criterion_id: str,
) -> tuple[int, str]:
    if negative and (_looks_negative(negative) or not positive):
        if must_have:
            return 0, "Contrary language found and this is a must-have."
        return 0, "Contrary language found and no supporting evidence."
    if criterion_id == "years_experience":
        return _score_years(years, positive)
    if positive and negative:
        return 1, "Mixed evidence: a positive indicator and a caution both appear."
    if positive:
        strength = 3 if len(positive) > 40 else 2
        return strength, "Supporting evidence found in the packet."
    if must_have:
        return 0, "No supporting evidence found for this must-have."
    return 0, "No supporting evidence found."


def _years_quote(packet: str, criterion: Criterion, years: float | None, existing: str) -> str:
    """Prefer an explicit years span so the quote shown to the operator is the number."""
    if criterion.id != "years_experience" or years is None or years < MIN_YEARS:
        return existing
    return existing or first_matching_window(packet, [f"{int(years)} years", f"{years:g} years", "years"])


def score_criteria(packet: str, role: RoleConfig) -> list[CriterionScore]:
    years = years_mentioned(packet)
    results: list[CriterionScore] = []
    for criterion in role.criteria:
        pos = first_matching_window(packet, criterion.indicators)
        neg = first_matching_window(packet, criterion.negative_indicators)
        score, rationale = _score_from_hits(pos, neg, criterion.must_have, years, criterion.id)
        pos = _years_quote(packet, criterion, years, pos)
        results.append(
            CriterionScore(
                id=criterion.id,
                label=criterion.label,
                score=score,
                must_have=criterion.must_have,
                weight=criterion.weight,
                evidence_quote=pos if score > 0 else "",
                evidence_found=bool(pos) and score > 0,
                rationale=rationale,
                contrary_evidence=neg,
            )
        )
    return results


def find_knockouts(packet: str, role: RoleConfig) -> list[KnockoutHit]:
    hits: list[KnockoutHit] = []
    for item in role.knockouts:
        quote = first_matching_window(packet, item.indicators)
        if quote:
            hits.append(
                KnockoutHit(
                    id=item.id,
                    label=item.label,
                    evidence_quote=quote,
                    action=NextAction(item.action),
                )
            )
    return hits


def find_risk_flags(packet: str, role: RoleConfig) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    for item in role.risk_flags:
        quote = first_matching_window(packet, item.indicators)
        if quote:
            flags.append(RiskFlag(id=item.id, label=item.label, evidence_quote=quote))
    return flags


def overall_score(criteria: list[CriterionScore]) -> float:
    if not criteria:
        return 0.0
    total_w = sum(c.weight for c in criteria) or 1.0
    earned = sum((c.score / c.max_score) * c.weight for c in criteria)
    return round(min(1.0, max(0.0, earned / total_w)), 3)
