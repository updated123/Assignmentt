from __future__ import annotations

from .config import app_config
from .schemas import CriterionScore, KnockoutHit, MissingField, NextAction, RiskFlag
from .textutil import too_short, years_mentioned

ACTION_LABELS = {
    NextAction.ADVANCE_TO_SCREEN: "Advance to screen",
    NextAction.HOLD_FOR_SPECIFIC_INTERVIEW: "Hold for a targeted interview",
    NextAction.REQUEST_MISSING_INFO: "Request missing information",
    NextAction.REJECT_WITH_REASON: "Reject with a reason",
    NextAction.ESCALATE_TO_HIRING_MANAGER: "Escalate to hiring manager",
}


def confidence_of(
    criteria: list[CriterionScore],
    knockouts: list[KnockoutHit],
    packet: str,
    missing: list[MissingField],
    llm_used: bool,
    warnings: list[str],
) -> float:
    cfg = app_config()
    min_chars = cfg.min_packet_chars
    if too_short(packet, min_chars * 2):
        base = 0.34
    else:
        evidenced = [c for c in criteria if c.evidence_found or (c.must_have and c.score == 0)]
        coverage = len(evidenced) / max(1, len(criteria))
        base = 0.45 + 0.4 * coverage
    if knockouts:
        base = min(base, 0.7)
    if missing:
        base -= 0.08 * min(3, len(missing))
    if not llm_used:
        base -= 0.05
    if warnings:
        base -= 0.04 * min(3, len(warnings))
    return round(min(0.95, max(0.15, base)), 2)


def confidence_label(value: float) -> str:
    bands = app_config().confidence
    if value >= bands.high:
        return "high"
    if value >= bands.medium:
        return "medium"
    return "low"


def missing_fields(packet: str, criteria: list[CriterionScore], candidate_name: str) -> list[MissingField]:
    missing: list[MissingField] = []
    if not candidate_name or candidate_name.lower().startswith("unknown"):
        missing.append(
            MissingField(
                field="candidate_name",
                why_it_matters="The review and any draft email need a verified name.",
                ask_back_question="What is the candidate’s full name as they would like it used?",
            )
        )
    if years_mentioned(packet) is None:
        missing.append(
            MissingField(
                field="years_experience",
                why_it_matters="The scorecard requires 3+ years; implied seniority is not enough.",
                ask_back_question="How many years of professional software engineering experience do they have?",
            )
        )
    for criterion in criteria:
        if criterion.must_have and criterion.score == 0:
            missing.append(
                MissingField(
                    field=criterion.id,
                    why_it_matters=f"Must-have is unproven: {criterion.label}",
                    ask_back_question=f"Can you share a production example related to: {criterion.label}?",
                )
            )
    # Deduplicate by field
    seen = set()
    unique = []
    for item in missing:
        if item.field in seen:
            continue
        seen.add(item.field)
        unique.append(item)
    return unique


def interview_probes(criteria: list[CriterionScore], flags: list[RiskFlag]) -> list[str]:
    probes = []
    for criterion in criteria:
        if criterion.must_have and criterion.score <= 2:
            probes.append(f"Ask for a production story that proves: {criterion.label}.")
        elif not criterion.must_have and criterion.score == 0:
            probes.append(f"Optional probe: {criterion.label}.")
    for flag in flags:
        probes.append(f"Risk probe: {flag.label}. Ask for context around: “{flag.evidence_quote[:90]}”.")
    # Keep the most useful 5
    return probes[:5]


def decide_action(
    *,
    packet: str,
    criteria: list[CriterionScore],
    knockouts: list[KnockoutHit],
    flags: list[RiskFlag],
    missing: list[MissingField],
    recruiter_notes: str,
    overall: float,
) -> tuple[NextAction, str, bool]:
    """Returns action, why, and whether policy overrode a would-be model suggestion."""
    cfg = app_config().action_policy
    advance_must = cfg.advance_min_must_have_score
    advance_overall = cfg.advance_min_overall
    hold_overall = cfg.hold_min_overall
    notes = (recruiter_notes or "") + "\n" + packet
    notes_l = notes.lower()
    is_referral = any(
        token in notes_l
        for token in ["referral", "referred by", "ceo asked", "founder asked", "internal rec", "board member"]
    )
    # Seniority shows up either as a title or as a years count. The title list is
    # vocabulary and stays here; the years bar is a configured number, because
    # "18 years" as a literal only caught the one packet that happened to say 18.
    senior_title = any(
        token in notes_l for token in ["vp engineering", "vice president", "head of engineering"]
    )
    stated_years = years_mentioned(notes)
    overqualified = senior_title or (
        stated_years is not None and stated_years >= cfg.overqualified_min_years
    )

    if any(k.id == "hostile_conduct" for k in knockouts) and not is_referral:
        return NextAction.REJECT_WITH_REASON, "A conduct knockout fired. Do not advance.", False

    if is_referral:
        return (
            NextAction.ESCALATE_TO_HIRING_MANAGER,
            "Founder, board, or internal referral. Policy forbids auto-close; a hiring manager must decide.",
            True,
        )

    if overqualified:
        return (
            NextAction.ESCALATE_TO_HIRING_MANAGER,
            "Senior/VP signal on a mid-level req. A human must confirm level and band.",
            True,
        )

    if any(k.id == "wrong_discipline" for k in knockouts):
        return NextAction.REJECT_WITH_REASON, "Packet looks like a different discipline with no web/product-engineering evidence.", False

    must = [c for c in criteria if c.must_have]
    unknown_musts = [c for c in must if c.score == 0 and not c.contrary_evidence]
    failed_musts = [c for c in must if c.score == 0 and c.contrary_evidence]
    weak_musts = [c for c in must if 0 < c.score < advance_must]
    evidenced_musts = [c for c in must if c.score >= 2]
    stub = len(packet) < 450 and len(evidenced_musts) < 2

    if failed_musts:
        hard = [c for c in failed_musts if c.id != "years_experience"]
        if hard:
            labels = ", ".join(c.label for c in hard)
            return NextAction.REJECT_WITH_REASON, f"Must-have appears to fail: {labels}.", False
        if stub:
            return (
                NextAction.REQUEST_MISSING_INFO,
                "Years/seniority is unclear on a thin packet. Ask for a complete resume rather than rejecting.",
                False,
            )
        labels = ", ".join(c.label for c in failed_musts)
        return NextAction.REJECT_WITH_REASON, f"Must-have appears to fail: {labels}.", False

    if stub or (unknown_musts and (len(packet) < 500 or overall < 0.4)):
        return (
            NextAction.REQUEST_MISSING_INFO,
            "Too little evidence to accept or reject a must-have. Ask for a complete packet.",
            False,
        )

    if unknown_musts and overall < advance_overall:
        labels = ", ".join(c.label for c in unknown_musts)
        return NextAction.REQUEST_MISSING_INFO, f"Must-haves are unproven rather than disproven: {labels}.", False

    if any(c.score == 0 for c in must) and not unknown_musts:
        labels = ", ".join(c.label for c in must if c.score == 0)
        return NextAction.REJECT_WITH_REASON, f"Must-have not evidenced: {labels}.", False

    if weak_musts or (hold_overall <= overall < advance_overall):
        gap = weak_musts[0].label if weak_musts else "mixed scorecard"
        return (
            NextAction.HOLD_FOR_SPECIFIC_INTERVIEW,
            f"Do not blanket-advance. Probe this gap first: {gap}.",
            False,
        )

    if any(f.id == "timezone_conflict" for f in flags) and overall >= advance_overall:
        return (
            NextAction.HOLD_FOR_SPECIFIC_INTERVIEW,
            "Skills look sufficient, but availability/timezone overlap is unresolved.",
            False,
        )

    if overall >= advance_overall and all(c.score >= advance_must for c in must):
        return NextAction.ADVANCE_TO_SCREEN, "Must-haves are evidenced and overall score clears the advance bar.", False

    return NextAction.HOLD_FOR_SPECIFIC_INTERVIEW, "Score is mixed. Keep a human in the loop with a targeted probe.", False
