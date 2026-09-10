"""The evaluation harness is itself a deliverable, so it gets tests.

The specific thing being guarded: a wide ``allowed_actions`` set must not be able
to report itself as a clean pass. v1.0 printed 12/12 while one case returned an
action that was not the expected one, because "pass" only ever meant "inside the
allowed set". These tests pin the three-way outcome that replaced it.
"""

from __future__ import annotations

import json

import pytest

from evaluation.run_eval import (
    OUTCOME_ALTERNATE,
    OUTCOME_EXACT,
    OUTCOME_FAIL,
    _action_outcome,
    build_report,
    load_cases,
    render_markdown,
    score_case,
)

CASE = {
    "id": "T",
    "expected_action": "hold_for_specific_interview",
    "allowed_actions": ["hold_for_specific_interview", "reject_with_reason"],
}


# --------------------------------------------------------------------------- #
# Outcome classification
# --------------------------------------------------------------------------- #


def test_matching_the_gold_action_is_exact():
    assert _action_outcome(CASE, "hold_for_specific_interview") == OUTCOME_EXACT


def test_an_allowed_but_different_action_is_alternate_not_exact():
    assert _action_outcome(CASE, "reject_with_reason") == OUTCOME_ALTERNATE


def test_an_action_outside_the_allowed_set_fails():
    assert _action_outcome(CASE, "advance_to_screen") == OUTCOME_FAIL


def test_the_expected_action_is_always_allowed_even_if_omitted():
    case = {"id": "T", "expected_action": "advance_to_screen", "allowed_actions": ["reject_with_reason"]}
    assert _action_outcome(case, "advance_to_screen") == OUTCOME_EXACT


# --------------------------------------------------------------------------- #
# Gold set hygiene
# --------------------------------------------------------------------------- #


def test_gold_set_covers_representative_edge_and_failure_cases():
    kinds = {case.get("kind") for case in load_cases()}
    assert {"representative", "edge", "failure"} <= kinds


def test_gold_set_is_between_eight_and_twelve_cases():
    assert 8 <= len(load_cases()) <= 12


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_every_gold_case_is_well_formed(case):
    assert case["expected_action"] in (case.get("allowed_actions") or [case["expected_action"]])
    allowed = case.get("allowed_actions") or [case["expected_action"]]
    assert len(allowed) <= 2, (
        f"{case['id']} allows {len(allowed)} actions; a set that wide stops being a test"
    )
    overlap = set(allowed) & set(case.get("must_not_action") or [])
    assert not overlap, f"{case['id']} both allows and forbids {overlap}"


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_every_gold_case_file_exists_and_is_substantial(case):
    from evaluation.run_eval import ROOT

    path = ROOT / case["file"]
    assert path.exists(), f"{case['id']} points at a missing file"
    assert len(path.read_text(encoding="utf-8")) > 150


# --------------------------------------------------------------------------- #
# The baseline must be a fair opponent, not a straw man
# --------------------------------------------------------------------------- #


def test_baseline_is_not_scored_on_grounding_it_cannot_produce():
    """Scoring a quote-less baseline on quote grounding would be circular."""
    case = next(c for c in load_cases() if c.get("require_evidence"))
    row = score_case(case, "baseline")
    assert "scores_are_grounded" not in row["checks"]
    assert "no_hallucinated_quotes" not in row["checks"]


def test_baseline_gets_some_cases_right():
    """A baseline that never wins is evidence of rigging, not of system quality."""
    cases = load_cases()
    exact = sum(1 for case in cases if score_case(case, "baseline")["outcome"] == OUTCOME_EXACT)
    assert exact >= 2, "a credible baseline should handle the easy packets"


def test_system_is_scored_on_grounding():
    case = next(c for c in load_cases() if c.get("require_evidence"))
    row = score_case(case, "system")
    assert row["checks"]["scores_are_grounded"] is True
    assert row["checks"]["no_hallucinated_quotes"] is True


# --------------------------------------------------------------------------- #
# Report shape
# --------------------------------------------------------------------------- #


def test_report_is_serialisable_and_renders(tmp_path):
    report = build_report(include_llm_path=False)
    json.dumps(report)  # must not raise
    markdown = render_markdown(report)
    assert "Exact match on the gold action" in markdown
    assert "Baseline cases" in markdown
    assert report["system"]["n"] == len(load_cases())


def test_report_separates_exact_from_alternate():
    report = build_report(include_llm_path=False)
    counts = report["system"]
    assert counts["exact"] + counts["alternate"] + counts["fail"] == counts["n"]
    assert counts["fail"] == 0, "the shipped system should have no out-of-set actions"


def test_report_records_that_no_draft_is_auto_sendable():
    report = build_report(include_llm_path=False)
    assert report["human_approval"]["drafts_auto_sendable"] == 0
    assert report["human_approval"]["reviews_requiring_approval"] == report["system"]["n"]
