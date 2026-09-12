"""Action policy and pipeline behaviour.

The policy is the part of this system that has to be arguable in a hiring
meeting, so each rule gets a named test rather than being implied by the
end-to-end evaluation.
"""

from __future__ import annotations

import pytest

from packet_review_os.pipeline import run_review
from packet_review_os.policy import confidence_label, decide_action, missing_fields
from packet_review_os.schemas import CriterionScore, KnockoutHit, NextAction, PacketInput, RiskFlag


def _crit(cid, score, must_have=True, contrary="", quote="x", weight=1.0):
    return CriterionScore(
        id=cid,
        label=cid.replace("_", " ").title(),
        score=score,
        must_have=must_have,
        weight=weight,
        evidence_quote=quote,
        evidence_found=bool(quote),
        contrary_evidence=contrary,
    )


def _decide(**kwargs):
    args = {
        "packet": "x" * 900,
        "criteria": [],
        "knockouts": [],
        "flags": [],
        "missing": [],
        "recruiter_notes": "",
        "overall": 0.8,
    }
    args.update(kwargs)
    return decide_action(**args)


# --------------------------------------------------------------------------- #
# Knockouts and escalation
# --------------------------------------------------------------------------- #


def test_conduct_knockout_rejects_even_with_perfect_skills():
    action, why, _ = _decide(
        criteria=[_crit("python_backend", 3), _crit("react_frontend", 3)],
        knockouts=[KnockoutHit(id="hostile_conduct", label="Hostile conduct", evidence_quote="q")],
    )
    assert action is NextAction.REJECT_WITH_REASON
    assert "conduct" in why.lower()


def test_referral_escalates_rather_than_auto_closing():
    """A founder referral is a political decision, so a human makes it."""
    action, _, overrode = _decide(
        criteria=[_crit("python_backend", 0, contrary="no python")],
        recruiter_notes="Referred by our CEO, please take a look.",
    )
    assert action is NextAction.ESCALATE_TO_HIRING_MANAGER
    assert overrode is True


def test_referral_with_a_conduct_knockout_escalates_deliberately():
    """Documented trade-off: a referral plus a conduct flag needs a human, not an auto-reject."""
    action, _, _ = _decide(
        criteria=[_crit("python_backend", 3)],
        knockouts=[KnockoutHit(id="hostile_conduct", label="Hostile conduct", evidence_quote="q")],
        recruiter_notes="Referred by a board member.",
    )
    assert action is NextAction.ESCALATE_TO_HIRING_MANAGER


def test_overqualified_signal_escalates():
    action, why, overrode = _decide(
        criteria=[_crit("python_backend", 3)],
        packet="VP Engineering with 18 years leading platform teams. " + "x" * 900,
    )
    assert action is NextAction.ESCALATE_TO_HIRING_MANAGER
    assert overrode is True
    assert "level" in why.lower() or "band" in why.lower()


def test_seniority_escalates_on_a_years_count_alone():
    """The bar is a number, not the wording of the one packet we had.

    This previously matched the literal string "18 years", so a 20-year
    candidate with no VP in their title was screened against a mid-level
    scorecard instead of being escalated.
    """
    action, _, overrode = _decide(
        criteria=[_crit("python_backend", 3)],
        packet="Independent consultant, 20 years building distributed systems. " + "x" * 900,
    )
    assert action is NextAction.ESCALATE_TO_HIRING_MANAGER
    assert overrode is True


def test_a_mid_level_years_count_does_not_escalate():
    action, _, _ = _decide(
        criteria=[_crit("python_backend", 3)],
        packet="Full-stack engineer, 5 years shipping product. " + "x" * 900,
    )
    assert action is not NextAction.ESCALATE_TO_HIRING_MANAGER


def test_wrong_discipline_knockout_rejects():
    action, _, _ = _decide(
        criteria=[_crit("python_backend", 0, contrary="ios only")],
        knockouts=[KnockoutHit(id="wrong_discipline", label="Wrong discipline", evidence_quote="Swift")],
    )
    assert action is NextAction.REJECT_WITH_REASON


# --------------------------------------------------------------------------- #
# Must-have gates: unproven is not the same as disproven
# --------------------------------------------------------------------------- #


def test_disproven_must_have_is_rejected():
    action, _, _ = _decide(
        criteria=[_crit("python_backend", 0, contrary="No Python in production"), _crit("react_frontend", 3)],
        overall=0.5,
    )
    assert action is NextAction.REJECT_WITH_REASON


def test_unproven_must_have_asks_back_instead_of_rejecting():
    action, why, _ = _decide(
        criteria=[_crit("python_backend", 0, contrary="", quote=""), _crit("react_frontend", 2)],
        overall=0.3,
    )
    assert action is NextAction.REQUEST_MISSING_INFO
    assert "unproven" in why.lower() or "too little" in why.lower()


def test_thin_packet_asks_back_rather_than_judging_a_stub():
    action, _, _ = _decide(packet="Short note.", criteria=[_crit("python_backend", 0, quote="")], overall=0.2)
    assert action is NextAction.REQUEST_MISSING_INFO


def test_weak_must_have_holds_for_a_targeted_probe():
    action, why, _ = _decide(
        criteria=[_crit("python_backend", 1), _crit("react_frontend", 3)],
        overall=0.66,
    )
    assert action is NextAction.HOLD_FOR_SPECIFIC_INTERVIEW
    assert "probe" in why.lower() or "gap" in why.lower()


def test_all_must_haves_evidenced_advances():
    action, _, _ = _decide(
        criteria=[_crit("python_backend", 3), _crit("react_frontend", 3), _crit("years_experience", 3)],
        overall=0.9,
    )
    assert action is NextAction.ADVANCE_TO_SCREEN


def test_unresolved_timezone_holds_even_with_strong_skills():
    action, why, _ = _decide(
        criteria=[_crit("python_backend", 3), _crit("react_frontend", 3)],
        flags=[RiskFlag(id="timezone_conflict", label="Timezone", evidence_quote="GMT+8")],
        overall=0.9,
    )
    assert action is NextAction.HOLD_FOR_SPECIFIC_INTERVIEW
    assert "overlap" in why.lower() or "availability" in why.lower()


def test_job_hopping_flag_does_not_block_an_otherwise_strong_packet():
    """A tenure risk is shown to the human; it is not a silent veto."""
    action, _, _ = _decide(
        criteria=[_crit("python_backend", 3), _crit("react_frontend", 3), _crit("years_experience", 3)],
        flags=[RiskFlag(id="job_hopping", label="Short tenures", evidence_quote="2022-2023")],
        overall=0.9,
    )
    assert action is NextAction.ADVANCE_TO_SCREEN


# --------------------------------------------------------------------------- #
# Ask-back questions and confidence
# --------------------------------------------------------------------------- #


def test_missing_fields_ask_a_specific_answerable_question():
    fields = missing_fields("A short note with no years.", [_crit("python_backend", 0)], "Unknown candidate")
    assert fields
    for field in fields:
        assert field.ask_back_question.endswith("?")
        assert field.why_it_matters


def test_missing_fields_are_deduplicated():
    fields = missing_fields("no years here", [_crit("years_experience", 0)], "")
    ids = [f.field for f in fields]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.95, "high"), (0.75, "high"), (0.6, "medium"), (0.5, "medium"), (0.3, "low")],
)
def test_confidence_labels_follow_the_configured_bands(value, expected):
    assert confidence_label(value) == expected


# --------------------------------------------------------------------------- #
# Pipeline-level guarantees
# --------------------------------------------------------------------------- #


def test_every_review_requires_human_approval_and_never_authorises_sending(packet_text):
    for case in ["TC01", "TC04", "TC11"]:
        result = run_review(PacketInput(role_id="fullstack_engineer", packet_text=packet_text(case)))
        assert result.human_must_approve is True
        assert result.draft_email is not None
        assert result.draft_email.send_allowed is False


def test_short_packet_is_an_exception_not_a_decision():
    result = run_review(PacketInput(role_id="fullstack_engineer", packet_text="hi"))
    assert result.ok is False
    assert result.exceptions
    assert result.next_action is NextAction.REQUEST_MISSING_INFO


def test_oversized_packet_is_truncated_with_a_warning():
    result = run_review(PacketInput(role_id="fullstack_engineer", packet_text="python react " * 8000))
    assert any("truncated" in w for w in result.warnings)
    assert result.packet_chars <= 40_000


def test_unknown_role_raises_a_config_error():
    from packet_review_os.config import ConfigError

    with pytest.raises(ConfigError):
        run_review(PacketInput(role_id="not_a_role", packet_text="x" * 200))


def test_bad_job_url_degrades_the_review_without_failing_it(packet_text):
    result = run_review(
        PacketInput(
            role_id="fullstack_engineer",
            packet_text=packet_text("TC01"),
            job_url="http://169.254.169.254/latest/meta-data/",
        )
    )
    assert result.job_text_used is False
    assert any("refus" in w.lower() or "private" in w.lower() for w in result.warnings)
    assert result.next_action is NextAction.ADVANCE_TO_SCREEN, "the packet itself is still reviewable"


def test_identical_packets_produce_the_same_fingerprint(packet_text):
    text = packet_text("TC01")
    first = run_review(PacketInput(role_id="fullstack_engineer", packet_text=text))
    second = run_review(PacketInput(role_id="fullstack_engineer", packet_text=text))
    assert first.packet_fingerprint == second.packet_fingerprint
    assert first.run_id != second.run_id


def test_recruiter_notes_reach_the_policy(packet_text):
    """A referral in the notes field must escalate, not just one in the packet body."""
    result = run_review(
        PacketInput(
            role_id="fullstack_engineer",
            packet_text=packet_text("TC01"),
            recruiter_notes="Referred by our CEO.",
        )
    )
    assert result.next_action is NextAction.ESCALATE_TO_HIRING_MANAGER
