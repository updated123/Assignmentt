from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class NextAction(str, Enum):
    ADVANCE_TO_SCREEN = "advance_to_screen"
    HOLD_FOR_SPECIFIC_INTERVIEW = "hold_for_specific_interview"
    REQUEST_MISSING_INFO = "request_missing_info"
    REJECT_WITH_REASON = "reject_with_reason"
    ESCALATE_TO_HIRING_MANAGER = "escalate_to_hiring_manager"


class EngineMode(str, Enum):
    GROUNDED_EXTRACTIVE = "grounded_extractive"
    LLM_JUDGE = "llm_judge"
    LLM_FALLBACK_EXTRACTIVE = "llm_fallback_extractive"
    BASELINE_UNGROUNDED = "baseline_ungrounded"


class PacketInput(BaseModel):
    role_id: str
    packet_text: str = ""
    recruiter_notes: str = ""
    candidate_name: str = ""
    job_url: str = ""
    source: str = "paste"
    run_mode: str = "system"

    @field_validator("packet_text")
    @classmethod
    def strip_packet(cls, value: str) -> str:
        return (value or "").strip()


class CriterionScore(BaseModel):
    id: str
    label: str
    score: int = Field(ge=0, le=3)
    max_score: int = 3
    must_have: bool = False
    weight: float = 1.0
    evidence_quote: str = ""
    evidence_found: bool = False
    rationale: str = ""
    contrary_evidence: str = ""


class KnockoutHit(BaseModel):
    id: str
    label: str
    evidence_quote: str = ""
    action: NextAction = NextAction.REJECT_WITH_REASON


class RiskFlag(BaseModel):
    id: str
    label: str
    evidence_quote: str = ""


class MissingField(BaseModel):
    field: str
    why_it_matters: str
    ask_back_question: str


class DraftEmail(BaseModel):
    purpose: str
    subject: str
    body: str
    send_allowed: bool = False


class ReviewResult(BaseModel):
    ok: bool = True
    run_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    role_id: str
    role_title: str = ""
    company: str = ""
    candidate_name: str = "Unknown candidate"
    engine_mode: EngineMode = EngineMode.GROUNDED_EXTRACTIVE
    next_action: NextAction
    next_action_label: str = ""
    human_must_approve: bool = True
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_label: str = "medium"
    overall_score: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    why_this_action: str = ""
    criteria: list[CriterionScore] = Field(default_factory=list)
    knockouts: list[KnockoutHit] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    missing_fields: list[MissingField] = Field(default_factory=list)
    interview_probes: list[str] = Field(default_factory=list)
    draft_email: DraftEmail | None = None
    warnings: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    packet_chars: int = 0
    packet_fingerprint: str = ""
    job_text_used: bool = False
    pdf_used: bool = False
    llm_used: bool = False
    policy_overrode_model: bool = False
    model_name: str = ""
    logs: list[str] = Field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ReviewRecord(BaseModel):
    run_id: str
    created_at: str
    role_id: str
    candidate_name: str
    next_action: str
    confidence: float
    overall_score: float
    engine_mode: str
    approved: bool | None = None
    approver_note: str = ""
    latency_ms: int = 0
    warnings: list[str] = Field(default_factory=list)


class EvalCaseResult(BaseModel):
    case_id: str
    passed: bool
    expected_action: str
    actual_action: str
    checks: dict[str, bool]
    notes: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    confidence: float = 0.0
    overall_score: float = 0.0
