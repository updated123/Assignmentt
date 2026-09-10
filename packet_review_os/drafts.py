from __future__ import annotations

from .schemas import DraftEmail, NextAction, ReviewResult


def build_draft(result: ReviewResult) -> DraftEmail:
    name = result.candidate_name if not result.candidate_name.lower().startswith("unknown") else "there"
    role = result.role_title or "the role"
    company = result.company or "our team"

    if result.next_action == NextAction.ADVANCE_TO_SCREEN:
        subject = f"{company}: next step for {role}"
        body = (
            f"Hi {name},\n\n"
            f"Thank you for sharing your materials for the {role} role at {company}. "
            "We’d like to schedule a 30-minute screen to walk through a recent production system you owned "
            "(backend and UI) and how you collaborated with product/design.\n\n"
            "Please send two times that work in the next five business days, including your timezone.\n\n"
            "Thank you,\nHiring team"
        )
        purpose = "Screen invite draft — do not send until a human approves."
    elif result.next_action == NextAction.HOLD_FOR_SPECIFIC_INTERVIEW:
        gap = result.interview_probes[0] if result.interview_probes else "a specific skill gap"
        subject = f"{company}: quick clarification for {role}"
        body = (
            f"Hi {name},\n\n"
            f"We reviewed your packet for the {role} role. Before we book a full screen, "
            f"could you share a short written example so we can probe this fairly?\n\n"
            f"{gap}\n\n"
            "A paragraph plus a link (PR, repo, product, or write-up) is enough.\n\n"
            "Thank you,\nHiring team"
        )
        purpose = "Targeted clarification draft — human must confirm the gap."
    elif result.next_action == NextAction.REQUEST_MISSING_INFO:
        questions = "\n".join(f"- {m.ask_back_question}" for m in result.missing_fields[:4]) or "- Please send a full resume and a production project summary."
        subject = f"{company}: a few details missing for {role}"
        body = (
            f"Hi {name},\n\n"
            f"Thanks for your interest in the {role} role at {company}. "
            "We cannot complete a fair scorecard review yet. Could you send:\n\n"
            f"{questions}\n\n"
            "We’ll continue the review as soon as we have this.\n\n"
            "Thank you,\nHiring team"
        )
        purpose = "Missing-info ask-back — send only after approval."
    elif result.next_action == NextAction.ESCALATE_TO_HIRING_MANAGER:
        subject = f"ESCALATE: {result.candidate_name} / {role}"
        body = (
            f"Internal note, not a candidate email.\n\n"
            f"Candidate: {result.candidate_name}\n"
            f"Recommended action: escalate\n"
            f"Why: {result.why_this_action}\n"
            f"Summary: {result.summary}\n\n"
            "Please decide whether to screen, hold, or decline. The system will not close this packet."
        )
        purpose = "Internal escalation note — never send to the candidate as-is."
    else:
        subject = f"{company}: update on {role}"
        body = (
            f"Hi {name},\n\n"
            f"Thank you for taking the time to apply for the {role} role at {company}. "
            "We reviewed your materials against the scorecard for this opening and are not moving forward.\n\n"
            "We appreciate your interest and wish you well in your search.\n\n"
            "Thank you,\nHiring team"
        )
        purpose = "Rejection draft — human must approve; do not auto-send."

    return DraftEmail(purpose=purpose, subject=subject, body=body, send_allowed=False)
