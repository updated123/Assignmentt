"""End-to-end review pipeline: raw packet in, structured decision out.

Order of operations, and why:

1. **Assemble** the packet from paste + PDF text + recruiter notes + fetched JD.
   All sources are merged into one string so that every later quote is checkable
   against exactly what the reviewer saw.
2. **Guard** length. Too short is an *exception*, not a decision -- the system
   asks for more rather than judging a stub.
3. **Score** extractively, so each score has a quotable span.
4. **Optionally consult the model**, then re-enforce grounding on its output.
5. **Apply policy in code** to pick the next action. The model never picks it.
6. **Draft** the corresponding email with ``send_allowed = False``.

The result carries its own audit trail: engine mode, warnings, exceptions, the
log lines, a packet fingerprint, and whether policy overrode the model.
"""

from __future__ import annotations

import logging
import time
import uuid

from . import drafts, evidence, llm, policy
from .baseline import no_policy_action, no_policy_baseline
from .config import RoleConfig, app_config, load_role, settings
from .jd_fetch import JobFetchError, fetch_job_text
from .schemas import EngineMode, NextAction, PacketInput, ReviewResult
from .textutil import collapse_ws, extract_name_guess, redact_pii, sha16, too_short

logger = logging.getLogger("packet_review_os.pipeline")

BASELINE_MODE = "baseline"


def _append_log(logs: list[str], message: str) -> None:
    logs.append(message)
    logger.info(message)


def _assemble_packet(
    payload: PacketInput,
    pdf_text: str,
    fetched_jd: str | None,
    logs: list[str],
    warnings: list[str],
) -> tuple[str, str, bool]:
    """Merge every input source into one reviewable string.

    Returns (packet, raw_text_for_name_guess, job_text_used).
    """
    raw_for_name = "\n".join(
        part for part in [payload.packet_text or "", pdf_text, payload.recruiter_notes or ""] if part
    )
    packet = collapse_ws(payload.packet_text or "")
    if pdf_text:
        packet = collapse_ws(packet + "\n" + pdf_text)
        _append_log(logs, "Merged text extracted from uploaded PDF.")
    notes = collapse_ws(payload.recruiter_notes or "")
    if notes:
        packet = f"{packet}\n\nRECRUITER NOTES:\n{notes}".strip()

    job_text_used = False
    if fetched_jd:
        packet = f"{packet}\n\nJOB DESCRIPTION (fetched):\n{fetched_jd}".strip()
        job_text_used = True
        _append_log(logs, "Fetched job description and attached it to the packet context.")
    elif payload.job_url.strip():
        try:
            fetched = fetch_job_text(
                payload.job_url.strip(),
                allow_private=settings().allow_private_job_urls,
            )
            packet = f"{packet}\n\nJOB DESCRIPTION (fetched):\n{fetched}".strip()
            job_text_used = True
            _append_log(logs, "Fetched the job URL and attached it to the packet context.")
        except JobFetchError as exc:
            # A bad job URL degrades the review; it must not fail it.
            warnings.append(str(exc))
            _append_log(logs, f"Job URL fetch failed: {exc}")
    return packet, raw_for_name, job_text_used


def _run_baseline(packet: str, role: RoleConfig, logs: list[str]):
    criteria = no_policy_baseline(packet, role)
    _append_log(logs, "Baseline mode: indicator scoring with no grounding and no policy gates.")
    return (
        criteria,
        evidence.overall_score(criteria),
        no_policy_action(criteria),
        "Baseline decided on the weighted score alone, with no policy gates.",
    )


def _run_system(
    packet: str,
    role: RoleConfig,
    has_exceptions: bool,
    logs: list[str],
    warnings: list[str],
):
    criteria = evidence.score_criteria(packet, role)
    knockouts = evidence.find_knockouts(packet, role)
    _append_log(
        logs,
        f"Extractive scorer produced {len(criteria)} criterion scores and {len(knockouts)} knockouts.",
    )
    engine = EngineMode.GROUNDED_EXTRACTIVE
    llm_used = False
    model_name = ""

    if llm.llm_enabled() and not has_exceptions:
        try:
            payload_llm = llm.judge_with_llm(packet, role, criteria)
            criteria = llm.merge_llm_criteria(packet, role, criteria, payload_llm, warnings)
            llm_used = True
            engine = EngineMode.LLM_JUDGE
            model_name = settings().openai_model
            _append_log(logs, f"LLM judge merged ({model_name}); ungrounded claims were capped.")
        except llm.LLMError as exc:
            warnings.append(f"LLM unavailable, used the grounded extractive fallback ({exc}).")
            engine = EngineMode.LLM_FALLBACK_EXTRACTIVE
            _append_log(logs, warnings[-1])
    elif not llm.llm_enabled():
        _append_log(logs, "No OPENAI_API_KEY. Running the grounded extractive engine only.")

    return criteria, knockouts, engine, llm_used, model_name


def run_review(
    payload: PacketInput,
    *,
    pdf_text: str = "",
    fetched_jd: str | None = None,
) -> ReviewResult:
    """Review one packet. Raises ConfigError only if the role scorecard is unusable."""
    started = time.perf_counter()
    logs: list[str] = []
    warnings: list[str] = []
    exceptions: list[str] = []
    cfg = app_config()
    role = load_role(payload.role_id)

    packet, raw_for_name, job_text_used = _assemble_packet(payload, pdf_text, fetched_jd, logs, warnings)
    notes = collapse_ws(payload.recruiter_notes or "")

    if too_short(packet, cfg.min_packet_chars):
        exceptions.append(
            f"Packet is too short ({len(packet)} characters). Paste a resume, notes, or upload a PDF."
        )
    if len(packet) > cfg.max_packet_chars:
        packet = packet[: cfg.max_packet_chars]
        warnings.append(f"Packet truncated to {cfg.max_packet_chars} characters.")

    candidate_name = payload.candidate_name.strip() or extract_name_guess(raw_for_name) or "Unknown candidate"
    _append_log(logs, f"Candidate name resolved to {candidate_name}.")

    is_baseline = payload.run_mode == BASELINE_MODE
    knockouts = []
    flags = evidence.find_risk_flags(packet, role)
    missing = []
    llm_used = False
    policy_override = False
    model_name = ""

    if is_baseline:
        criteria, overall, action, why = _run_baseline(packet, role, logs)
        engine = EngineMode.BASELINE_UNGROUNDED
    else:
        criteria, knockouts, engine, llm_used, model_name = _run_system(
            packet, role, bool(exceptions), logs, warnings
        )
        overall = evidence.overall_score(criteria)
        missing = policy.missing_fields(packet, criteria, candidate_name)
        action, why, policy_override = policy.decide_action(
            packet=packet,
            criteria=criteria,
            knockouts=knockouts,
            flags=flags,
            missing=missing,
            recruiter_notes=notes,
            overall=overall,
        )
        _append_log(logs, f"Policy chose {action.value}. Override={policy_override}.")

    if exceptions and not is_baseline:
        action = NextAction.REQUEST_MISSING_INFO
        why = exceptions[0]
        warnings.append("Output is an exception path, not a completed hire/no-hire decision.")

    conf = policy.confidence_of(criteria, knockouts, packet, missing, llm_used, warnings)
    probes = [] if is_baseline else policy.interview_probes(criteria, flags)

    result = ReviewResult(
        ok=not exceptions,
        run_id=uuid.uuid4().hex[:12],
        role_id=role.role_id,
        role_title=role.title,
        company=role.company,
        candidate_name=candidate_name,
        engine_mode=engine,
        next_action=action,
        next_action_label=policy.ACTION_LABELS[action],
        human_must_approve=True,
        confidence=conf,
        confidence_label=policy.confidence_label(conf),
        overall_score=overall,
        summary=_summary(candidate_name, criteria, action, knockouts),
        why_this_action=why,
        criteria=criteria,
        knockouts=knockouts,
        risk_flags=flags,
        missing_fields=missing,
        interview_probes=probes,
        warnings=warnings,
        exceptions=exceptions,
        packet_chars=len(packet),
        packet_fingerprint=sha16(packet),
        job_text_used=job_text_used,
        pdf_used=bool(pdf_text),
        llm_used=llm_used,
        policy_overrode_model=policy_override,
        model_name=model_name,
        logs=logs,
    )
    if not is_baseline:
        result.draft_email = drafts.build_draft(result)
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def _summary(name: str, criteria, action: NextAction, knockouts) -> str:
    must = [c for c in criteria if c.must_have]
    met = [c.label for c in must if c.score >= 2]
    gaps = [c.label for c in must if c.score < 2]
    knockout_txt = f" Knockout: {knockouts[0].label}." if knockouts else ""
    met_txt = ", ".join(met) if met else "none of the must-haves clearly"
    gap_txt = ", ".join(gaps) if gaps else "no must-have gap"
    return (
        f"{name}: recommended {policy.ACTION_LABELS[action].lower()}. "
        f"Evidenced: {met_txt}. Gaps: {gap_txt}.{knockout_txt}"
    )


def safe_log_packet(text: str) -> str:
    """Trim and redact packet text before it is handed to the store."""
    body = text or ""
    if app_config().redact_emails_in_logs:
        body = redact_pii(body)
    return body[:4000]
