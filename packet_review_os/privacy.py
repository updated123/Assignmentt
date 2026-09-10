"""Redaction applied before anything is written to disk.

The README promises that stored packets have contact details removed. The v1.0
code only redacted the ``packet_redacted`` column, while the full ``result_json``
blob still carried the candidate's email and phone inside evidence quotes, the
summary, the draft email body and the log lines. This module closes that gap by
redacting every free-text field of a persisted review.

Redaction is deliberately conservative: it removes direct contact identifiers
(email, phone, and common profile URLs), not names. The candidate's name is the
operating subject of the review and is shown in the UI, so removing it would make
the audit log useless. Anyone handling real packets should read the privacy
section of docs/RUNBOOK.md.
"""

from __future__ import annotations

import re
from typing import Any

from .textutil import redact_pii

# The scheme is optional on purpose. Evidence quotes are windowed out of the
# packet, so a stored quote often begins mid-URL ("…/linkedin.com/in/name"),
# which a scheme-anchored pattern would miss.
PROFILE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|twitter\.com|x\.com)/\S+",
    re.I,
)

# Fields of a ReviewResult (and its nested models) that can carry packet text.
_TEXT_FIELDS = frozenset(
    {
        "summary",
        "why_this_action",
        "evidence_quote",
        "contrary_evidence",
        "rationale",
        "body",
        "subject",
        "ask_back_question",
        "why_it_matters",
        "approver_note",
    }
)
_TEXT_LIST_FIELDS = frozenset({"interview_probes", "warnings", "exceptions", "logs"})


def scrub_text(text: str) -> str:
    """Remove direct contact identifiers from a free-text string."""
    if not text:
        return text
    scrubbed = PROFILE_URL_RE.sub("[profile url redacted]", text)
    return redact_pii(scrubbed)


def scrub_payload(payload: Any) -> Any:
    """Recursively redact the free-text fields of a serialized review."""
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _TEXT_FIELDS and isinstance(value, str):
                out[key] = scrub_text(value)
            elif key in _TEXT_LIST_FIELDS and isinstance(value, list):
                out[key] = [scrub_text(v) if isinstance(v, str) else scrub_payload(v) for v in value]
            else:
                out[key] = scrub_payload(value)
        return out
    if isinstance(payload, list):
        return [scrub_payload(item) for item in payload]
    return payload
