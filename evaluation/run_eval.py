"""Evaluation harness.

Design notes, because the shape of this file is the argument:

**Three outcomes, not two.** A gold case carries one ``expected_action`` (the
label a reviewer would defend) and an ``allowed_actions`` set (other actions that
are defensible on the same packet). Reporting only pass/fail against the allowed
set lets a wide allowed set flatter the system: the v1.0 report showed 12/12 while
TC02 returned an action that was not the expected one. So every case now reports
``exact`` (matched the gold label), ``alternate`` (defensible but not the gold
label -- surfaced, never hidden) or ``fail``. The headline quotes exact matches.

**Invariants are scored separately from the action.** Whether the system fired
the right knockout, avoided a forbidden action, or grounded every score is
checked independently, so a case can return a defensible action and still fail.

**The baseline gets a fair fight.** It runs the same packets and is judged by the
same action rules. Grounding is *not* scored against it, because a single
unstructured prompt cannot emit per-criterion quotes by construction -- claiming
a win there would be circular. Grounding is reported as a capability the baseline
does not have. See ``packet_review_os/baseline.py`` for why the v1.0 baseline was
withdrawn.

**The model path is covered without a key.** ``run_llm_path_cases`` drives the
real request/parse/retry/fallback code through a stub HTTP transport, including a
hallucinated quote, malformed JSON, a 500, a timeout, and a model that tries to
advance a candidate with a conduct knockout.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packet_review_os.config import Settings  # noqa: E402
from packet_review_os.pipeline import run_review  # noqa: E402
from packet_review_os.schemas import EngineMode, PacketInput  # noqa: E402
from packet_review_os.textutil import quote_in_source  # noqa: E402

GOLD = Path(__file__).resolve().parent / "gold_cases.json"

OUTCOME_EXACT = "exact"
OUTCOME_ALTERNATE = "alternate"
OUTCOME_FAIL = "fail"

GROUNDED_SCORE_FLOOR = 2  # a score at or above this must be backed by a quote


def load_cases() -> list[dict]:
    return json.loads(GOLD.read_text(encoding="utf-8"))


def _action_outcome(case: dict, action: str) -> str:
    allowed = set(case.get("allowed_actions") or [case["expected_action"]])
    allowed.add(case["expected_action"])
    if action == case["expected_action"]:
        return OUTCOME_EXACT
    if action in allowed:
        return OUTCOME_ALTERNATE
    return OUTCOME_FAIL


def _grounding_report(result, packet: str) -> dict:
    """How much of the scorecard is backed by a verbatim quote."""
    scoring = [c for c in result.criteria if c.score >= GROUNDED_SCORE_FLOOR]
    grounded = [c for c in scoring if c.evidence_quote and quote_in_source(c.evidence_quote, packet)]
    hallucinated = [
        c.id for c in result.criteria if c.evidence_quote and not quote_in_source(c.evidence_quote, packet)
    ]
    return {
        "scoring_criteria": len(scoring),
        "grounded_criteria": len(grounded),
        "ungrounded_ids": [c.id for c in scoring if c not in grounded],
        "hallucinated_ids": hallucinated,
    }


def score_case(case: dict, mode: str) -> dict:
    """Run one gold case and check the action outcome plus every invariant."""
    text = (ROOT / case["file"]).read_text(encoding="utf-8")
    started = time.perf_counter()
    result = run_review(
        PacketInput(
            role_id=case.get("role_id", "fullstack_engineer"),
            packet_text=text,
            source="eval",
            run_mode="baseline" if mode == "baseline" else "system",
        )
    )
    latency = max(1, int((time.perf_counter() - started) * 1000))
    action = result.next_action.value
    outcome = _action_outcome(case, action)
    grounding = _grounding_report(result, text)

    checks: dict[str, bool] = {}
    notes: list[str] = []

    if case.get("must_not_action"):
        ok = action not in case["must_not_action"]
        checks["avoided_forbidden_action"] = ok
        if not ok:
            notes.append(f"Returned forbidden action {action}.")

    if case.get("must_detect_criteria"):
        by_id = {c.id: c for c in result.criteria}
        missed = [
            cid
            for cid in case["must_detect_criteria"]
            if cid not in by_id or by_id[cid].score < GROUNDED_SCORE_FLOOR
        ]
        checks["detected_required_criteria"] = not missed
        if missed:
            notes.append(f"Did not evidence: {', '.join(missed)}.")

    if case.get("require_frontend_gap"):
        react = next((c for c in result.criteria if c.id == "react_frontend"), None)
        ok = bool(react and react.score <= 1)
        checks["frontend_gap"] = ok
        if not ok:
            notes.append("Did not treat React as a gap.")

    if case.get("require_knockout"):
        ids = [k.id for k in result.knockouts]
        ok = case["require_knockout"] in ids
        checks["knockout_fired"] = ok
        if not ok:
            notes.append(f"Missing knockout {case['require_knockout']}.")

    if case.get("forbid_knockout"):
        ok = not result.knockouts
        checks["no_false_knockout"] = ok
        if not ok:
            notes.append(f"False knockout: {[k.id for k in result.knockouts]}.")

    if case.get("require_risk"):
        ids = [f.id for f in result.risk_flags]
        ok = case["require_risk"] in ids
        checks["risk_flagged"] = ok
        if not ok:
            notes.append(f"Missing risk flag {case['require_risk']}.")

    # Grounding invariants apply only to modes that claim to be grounded.
    # The baseline emits no quotes by construction; scoring it here would be circular.
    if mode != "baseline":
        checks["no_hallucinated_quotes"] = not grounding["hallucinated_ids"]
        if grounding["hallucinated_ids"]:
            notes.append(f"Hallucinated quotes: {grounding['hallucinated_ids']}.")
        if case.get("require_evidence"):
            ok = grounding["scoring_criteria"] > 0 and not grounding["ungrounded_ids"]
            checks["scores_are_grounded"] = ok
            if not ok:
                notes.append(f"Scores without a verbatim quote: {grounding['ungrounded_ids']}.")

    if outcome == OUTCOME_FAIL:
        notes.append(f"Action {action} is outside the allowed set {case.get('allowed_actions')}.")
    elif outcome == OUTCOME_ALTERNATE:
        notes.append(f"Defensible alternate: expected {case['expected_action']}, returned {action}.")

    passed = outcome != OUTCOME_FAIL and all(checks.values())
    return {
        "case_id": case["id"],
        "kind": case.get("kind"),
        "outcome": outcome,
        "passed": passed,
        "expected_action": case["expected_action"],
        "allowed_actions": case.get("allowed_actions") or [case["expected_action"]],
        "actual_action": action,
        "checks": checks,
        "notes": notes,
        "grounding": grounding,
        "latency_ms": latency,
        "confidence": result.confidence,
        "overall_score": result.overall_score,
        "engine_mode": result.engine_mode.value,
        "human_must_approve": result.human_must_approve,
        "draft_send_allowed": bool(result.draft_email and result.draft_email.send_allowed),
        "summary": result.summary,
    }


# --------------------------------------------------------------------------- #
# Model-path fixtures. These drive the real llm.py code through a stub
# transport, so the judge, the grounding enforcement, the retry policy and the
# fallback are all covered with no API key and no network.
# --------------------------------------------------------------------------- #


def _chat_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )


def _llm_settings(**overrides) -> Settings:
    base = {"openai_api_key": "test-key-not-real", "llm_retries": 1, "llm_timeout_seconds": 5.0}
    base.update(overrides)
    return Settings(**base)


def run_llm_path_cases() -> list[dict]:
    """Six fixtures over the optional model path."""
    from packet_review_os import config as config_mod
    from packet_review_os import llm as llm_mod

    packet_path = ROOT / "samples" / "packets" / "TC01_strong_match.txt"
    packet = packet_path.read_text(encoding="utf-8")
    conduct = (ROOT / "samples" / "packets" / "TC11_toxic_conduct.txt").read_text(encoding="utf-8")
    rows: list[dict] = []

    real_settings = config_mod.settings
    calls = {"n": 0}

    def with_stub(handler, packet_text: str, **settings_overrides):
        """Run one review with the model enabled and the stub transport installed."""
        calls["n"] = 0
        stub = _llm_settings(**settings_overrides)
        config_mod.settings = lambda: stub
        llm_mod.settings = lambda: stub
        try:
            with llm_mod.stub_transport(handler):
                return run_review(
                    PacketInput(role_id="fullstack_engineer", packet_text=packet_text, source="eval")
                )
        finally:
            config_mod.settings = real_settings
            llm_mod.settings = real_settings

    def counting(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return handler(request)

        return wrapped

    # LX01 - a well-formed grounded response is used, and the engine says so.
    good_quote = "Shipped Python FastAPI services"
    assert good_quote in packet, "fixture packet changed; update LX01 quote"

    def good(_request):
        return _chat_response(
            {
                "summary": "Strong full-stack packet.",
                "criteria": [
                    {
                        "id": "python_backend",
                        "score": 3,
                        "evidence_quote": good_quote,
                        "rationale": "Quoted from the packet.",
                    }
                ],
                "recommended_action": "advance_to_screen",
            }
        )

    result = with_stub(counting(good), packet)
    python_crit = next(c for c in result.criteria if c.id == "python_backend")
    rows.append(
        {
            "case_id": "LX01_valid_json_merged",
            "kind": "model-path",
            "passed": result.engine_mode is EngineMode.LLM_JUDGE
            and result.llm_used
            and python_crit.score == 3
            and quote_in_source(python_crit.evidence_quote, packet),
            "notes": [f"engine={result.engine_mode.value}, python_backend score={python_crit.score}"],
        }
    )

    # LX02 - a quote that is not in the packet is dropped and the score pulled back.
    def hallucinating(_request):
        return _chat_response(
            {
                "criteria": [
                    {
                        "id": "python_backend",
                        "score": 3,
                        "evidence_quote": "Led the Kubernetes migration at Google for four years.",
                        "rationale": "Invented.",
                    }
                ],
            }
        )

    result = with_stub(counting(hallucinating), packet)
    python_crit = next(c for c in result.criteria if c.id == "python_backend")
    dropped = any("hallucinated quote" in w.lower() for w in result.warnings)
    rows.append(
        {
            "case_id": "LX02_hallucinated_quote_dropped",
            "kind": "model-path",
            "passed": dropped and quote_in_source(python_crit.evidence_quote, packet),
            "notes": [w for w in result.warnings if "quote" in w.lower()] or ["no warning raised"],
        }
    )

    # LX03 - unparseable content falls back to the extractive engine.
    def malformed(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})

    result = with_stub(counting(malformed), packet)
    rows.append(
        {
            "case_id": "LX03_malformed_json_fallback",
            "kind": "model-path",
            "passed": result.engine_mode is EngineMode.LLM_FALLBACK_EXTRACTIVE
            and not result.llm_used
            and result.ok,
            "notes": [f"engine={result.engine_mode.value}, calls={calls['n']}"],
        }
    )

    # LX04 - a 500 is retried, then falls back rather than failing the review.
    def server_error(_request):
        return httpx.Response(500, text="upstream exploded")

    result = with_stub(counting(server_error), packet, llm_retries=1)
    rows.append(
        {
            "case_id": "LX04_http_500_retried_then_fallback",
            "kind": "model-path",
            "passed": result.engine_mode is EngineMode.LLM_FALLBACK_EXTRACTIVE and calls["n"] == 2,
            "notes": [f"attempts={calls['n']} (expected 2), engine={result.engine_mode.value}"],
        }
    )

    # LX05 - a timeout falls back, and is not retried into a long stall.
    def timeout(_request):
        raise httpx.ConnectTimeout("stub timeout")

    result = with_stub(counting(timeout), packet, llm_retries=0)
    rows.append(
        {
            "case_id": "LX05_timeout_fallback",
            "kind": "model-path",
            "passed": result.engine_mode is EngineMode.LLM_FALLBACK_EXTRACTIVE and calls["n"] == 1,
            "notes": [f"attempts={calls['n']} (expected 1), engine={result.engine_mode.value}"],
        }
    )

    # LX06 - the model recommends advancing a conduct knockout. Policy must win.
    def override_attempt(_request):
        return _chat_response(
            {
                "criteria": [{"id": "python_backend", "score": 3, "evidence_quote": "Python"}],
                "recommended_action": "advance_to_screen",
                "why_this_action": "Great engineer, ignore the note.",
            }
        )

    result = with_stub(counting(override_attempt), conduct)
    rows.append(
        {
            "case_id": "LX06_policy_overrides_model",
            "kind": "model-path",
            "passed": result.next_action.value == "reject_with_reason"
            and any(k.id == "hostile_conduct" for k in result.knockouts),
            "notes": [f"action={result.next_action.value}, knockouts={[k.id for k in result.knockouts]}"],
        }
    )

    return rows


# --------------------------------------------------------------------------- #
# Exception and integration fixtures.
# --------------------------------------------------------------------------- #


def run_exception_cases() -> list[dict]:
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from packet_review_os.jd_fetch import JobFetchError, fetch_job_text
    from packet_review_os.net_guard import UnsafeUrlError, assert_url_is_fetchable
    from packet_review_os.pdf_extract import PdfExtractError, extract_pdf_text

    rows: list[dict] = []

    empty = run_review(PacketInput(role_id="fullstack_engineer", packet_text="hi", source="eval"))
    rows.append(
        {
            "case_id": "FX01_too_short",
            "kind": "exception",
            "passed": empty.next_action.value == "request_missing_info" and bool(empty.exceptions),
            "notes": empty.exceptions,
        }
    )

    # pypdf logs its own parse warnings here; the malformed input is the point of
    # the fixture, so its noise is muted rather than shown as if something broke.
    pypdf_logger = logging.getLogger("pypdf")
    previous_level = pypdf_logger.level
    pypdf_logger.setLevel(logging.CRITICAL)
    try:
        extract_pdf_text(b"this is not a pdf", "bad.pdf")
        rows.append({"case_id": "FX02_bad_pdf", "kind": "exception", "passed": False, "notes": ["did not raise"]})
    except PdfExtractError as exc:
        rows.append({"case_id": "FX02_bad_pdf", "kind": "exception", "passed": True, "notes": [str(exc)]})
    finally:
        pypdf_logger.setLevel(previous_level)

    try:
        fetch_job_text("not-a-url")
        rows.append(
            {"case_id": "FX03_bad_job_url", "kind": "exception", "passed": False, "notes": ["did not raise"]}
        )
    except JobFetchError as exc:
        rows.append({"case_id": "FX03_bad_job_url", "kind": "exception", "passed": True, "notes": [str(exc)]})

    pdf_path = ROOT / "samples" / "resumes" / "priya_nair.pdf"
    try:
        pdf_text = extract_pdf_text(pdf_path.read_bytes(), pdf_path.name)
        ok = "python" in pdf_text.lower() and "react" in pdf_text.lower()
        rows.append(
            {"case_id": "FX04_pdf_text", "kind": "integration", "passed": ok, "notes": [pdf_text[:140]]}
        )
    except Exception as exc:  # noqa: BLE001
        rows.append({"case_id": "FX04_pdf_text", "kind": "integration", "passed": False, "notes": [str(exc)]})

    # FX05 - real HTTP fetch against a local server. The SSRF guard blocks
    # loopback by default, so this fixture opts in explicitly, which is also the
    # assertion that the opt-in is required.
    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, fmt, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/samples/jd/northloop_fullstack.html"
        fetched = fetch_job_text(url, allow_private=True)
        rows.append(
            {
                "case_id": "FX05_job_url_fetch",
                "kind": "integration",
                "passed": "FastAPI" in fetched and "React" in fetched,
                "notes": [fetched[:140]],
            }
        )
        blocked = False
        try:
            fetch_job_text(url)
        except JobFetchError:
            blocked = True
        rows.append(
            {
                "case_id": "FX06_ssrf_loopback_blocked",
                "kind": "security",
                "passed": blocked,
                "notes": ["Loopback job URL refused unless allow_private is set."],
            }
        )
    except JobFetchError as exc:
        rows.append({"case_id": "FX05_job_url_fetch", "kind": "integration", "passed": False, "notes": [str(exc)]})
    finally:
        server.shutdown()

    metadata_blocked = True
    metadata_note = "Cloud metadata IP refused."
    try:
        assert_url_is_fetchable("http://169.254.169.254/latest/meta-data/")
        metadata_blocked = False
        metadata_note = "Metadata IP was NOT blocked."
    except UnsafeUrlError as exc:
        metadata_note = str(exc)[:140]
    rows.append(
        {"case_id": "FX07_ssrf_metadata_blocked", "kind": "security", "passed": metadata_blocked, "notes": [metadata_note]}
    )

    return rows


# --------------------------------------------------------------------------- #
# Aggregation and reporting.
# --------------------------------------------------------------------------- #


def _counts(rows: list[dict]) -> dict:
    outcomes = Counter(r["outcome"] for r in rows)
    latencies = [r["latency_ms"] for r in rows]
    return {
        "n": len(rows),
        "exact": outcomes[OUTCOME_EXACT],
        "alternate": outcomes[OUTCOME_ALTERNATE],
        "fail": outcomes[OUTCOME_FAIL],
        "passed": sum(1 for r in rows if r["passed"]),
        "invariant_failures": sorted(
            {name for r in rows for name, ok in r["checks"].items() if not ok}
        ),
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "latency_p95_ms": max(latencies) if len(latencies) < 20 else int(statistics.quantiles(latencies, n=20)[18]),
        "action_histogram": dict(Counter(r["actual_action"] for r in rows)),
    }


def _false_advances(rows: list[dict], cases: list[dict]) -> list[str]:
    out = []
    for row, case in zip(rows, cases, strict=True):
        if "advance_to_screen" in (case.get("must_not_action") or []) and row["actual_action"] == "advance_to_screen":
            out.append(row["case_id"])
    return out


def build_report(include_llm_path: bool = True) -> dict:
    cases = load_cases()
    system_rows = [score_case(case, "system") for case in cases]
    baseline_rows = [score_case(case, "baseline") for case in cases]
    exceptions = run_exception_cases()
    llm_rows = run_llm_path_cases() if include_llm_path else []

    system = _counts(system_rows)
    baseline = _counts(baseline_rows)

    grounded = sum(r["grounding"]["grounded_criteria"] for r in system_rows)
    scoring = sum(r["grounding"]["scoring_criteria"] for r in system_rows)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engine_mode": system_rows[0]["engine_mode"] if system_rows else "unknown",
        "system": system,
        "baseline": baseline,
        "system_false_advances": _false_advances(system_rows, cases),
        "baseline_false_advances": _false_advances(baseline_rows, cases),
        "grounding_coverage": {
            "system_scoring_criteria": scoring,
            "system_grounded_criteria": grounded,
            "system_pct": round(100.0 * grounded / scoring, 1) if scoring else 0.0,
            "baseline_pct": None,
            "baseline_note": "Not scored. A single unstructured prompt cannot emit per-criterion quotes.",
        },
        "human_approval": {
            "reviews_requiring_approval": sum(1 for r in system_rows if r["human_must_approve"]),
            "drafts_auto_sendable": sum(1 for r in system_rows if r["draft_send_allowed"]),
        },
        "exception_pass": sum(1 for r in exceptions if r["passed"]),
        "exception_n": len(exceptions),
        "llm_path_pass": sum(1 for r in llm_rows if r["passed"]),
        "llm_path_n": len(llm_rows),
        "cases": system_rows,
        "baseline_cases": baseline_rows,
        "exceptions": exceptions,
        "llm_path": llm_rows,
    }


def render_markdown(report: dict) -> str:
    sys_c = report["system"]
    base_c = report["baseline"]
    ground = report["grounding_coverage"]
    lines = [
        "# Evaluation results",
        "",
        f"Generated: {report['generated_at']} · engine: `{report['engine_mode']}`",
        "",
        "## Headline",
        "",
        "| Metric | System | Baseline (no policy, no grounding) |",
        "|---|---|---|",
        f"| Exact match on the gold action | **{sys_c['exact']}/{sys_c['n']}** | {base_c['exact']}/{base_c['n']} |",
        f"| Defensible alternate action | {sys_c['alternate']} | {base_c['alternate']} |",
        f"| Outside the allowed set (fail) | **{sys_c['fail']}** | {base_c['fail']} |",
        f"| Cases passing every invariant | **{sys_c['passed']}/{sys_c['n']}** | {base_c['passed']}/{base_c['n']} |",
        f"| False advances on must-not-advance cases | **{len(report['system_false_advances'])}** "
        f"| {len(report['baseline_false_advances'])} |",
        f"| Scores backed by a verbatim quote | **{ground['system_pct']}%** "
        f"({ground['system_grounded_criteria']}/{ground['system_scoring_criteria']}) | not available |",
        f"| Latency p50 / p95 | {sys_c['latency_p50_ms']} ms / {sys_c['latency_p95_ms']} ms "
        f"| {base_c['latency_p50_ms']} ms / {base_c['latency_p95_ms']} ms |",
        "",
        f"- Baseline false advances: {report['baseline_false_advances'] or 'none'}",
        f"- System false advances: {report['system_false_advances'] or 'none'}",
        f"- Model-path fixtures: {report['llm_path_pass']}/{report['llm_path_n']}",
        f"- Exception & security fixtures: {report['exception_pass']}/{report['exception_n']}",
        f"- Drafts marked auto-sendable: {report['human_approval']['drafts_auto_sendable']} "
        f"(every one of {sys_c['n']} reviews requires human approval)",
        "",
        "`exact` = matched the gold action. `alternate` = a different action that the case",
        "explicitly allows; it is reported separately so a wide allowed set cannot flatter",
        "the score. `fail` = outside the allowed set, or an invariant broke.",
        "",
        "## System cases",
        "",
        "| Case | Kind | Outcome | Expected | Actual | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["cases"]:
        notes = "; ".join(row.get("notes") or []) or "—"
        lines.append(
            f"| {row['case_id']} | {row.get('kind')} | {row['outcome'].upper()} "
            f"| {row['expected_action']} | {row['actual_action']} | {notes} |"
        )

    lines += [
        "",
        "## Baseline cases (same packets, no policy layer, no grounding)",
        "",
        "| Case | Outcome | Expected | Baseline action |",
        "|---|---|---|---|",
    ]
    for row in report["baseline_cases"]:
        lines.append(
            f"| {row['case_id']} | {row['outcome'].upper()} | {row['expected_action']} | {row['actual_action']} |"
        )

    lines += ["", "## Model-path fixtures (stub transport, no API key needed)", ""]
    for row in report["llm_path"]:
        lines.append(
            f"- {row['case_id']}: {'PASS' if row['passed'] else 'FAIL'} — {'; '.join(row.get('notes') or [])}"
        )

    lines += ["", "## Exception, integration and security fixtures", ""]
    for row in report["exceptions"]:
        lines.append(
            f"- {row['case_id']} ({row.get('kind')}): {'PASS' if row['passed'] else 'FAIL'} "
            f"— {'; '.join(row.get('notes') or [])}"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_eval", description="Run the Packet Review OS evaluation set.")
    parser.add_argument("--skip-llm-path", action="store_true", help="Skip the stub-transport model fixtures.")
    parser.add_argument("--quiet", action="store_true", help="Write the report files without printing them.")
    parser.add_argument("--verbose", action="store_true", help="Show per-review INFO logs while evaluating.")
    args = parser.parse_args(argv)

    # A 12-case run emits ~90 INFO lines. Default to warnings so the report is
    # the output; --verbose restores the per-review trace for debugging.
    if not args.verbose:
        for name in ("packet_review_os", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    report = build_report(include_llm_path=not args.skip_llm_path)
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    (out_dir / "LATEST.md").write_text(markdown, encoding="utf-8")
    if not args.quiet:
        print(markdown)

    healthy = (
        report["system"]["fail"] == 0
        and report["system"]["passed"] == report["system"]["n"]
        and report["exception_pass"] == report["exception_n"]
        and report["llm_path_pass"] == report["llm_path_n"]
    )
    if not healthy:
        print("EVALUATION GATE FAILED — see the tables above.", file=sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
