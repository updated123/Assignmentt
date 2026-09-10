"""Configuration loading and validation.

Two kinds of configuration, deliberately separated:

* **Secrets and deployment settings** come from the environment / ``.env``
  (:class:`Settings`). Nothing secret is ever read from a YAML file in the repo.
* **Business rules** come from ``config/app.yaml`` and ``config/roles/*.yaml``
  (:class:`AppConfig`, :class:`RoleConfig`). These are the files a non-developer
  is expected to edit, so they are validated up front with messages that name
  the file and the field.

v1.0 loaded both as raw dicts, so a typo like ``advance_min_overall: "hgih"``
surfaced as a ``ValueError`` from deep inside the scorer on the first real
packet. Now ``python -m packet_review_os check`` catches it before a review runs.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("packet_review_os.config")

ROOT = Path(__file__).resolve().parent.parent

VALID_ACTIONS = frozenset(
    {
        "advance_to_screen",
        "hold_for_specific_interview",
        "request_missing_info",
        "reject_with_reason",
        "escalate_to_hiring_manager",
    }
)


class ConfigError(RuntimeError):
    """Raised when configuration on disk is unusable. Message is operator-facing."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_path: str = "data/packet_review.db"
    log_level: str = "INFO"

    # Operator safety switches.
    api_token: str = ""
    allow_private_job_urls: bool = False
    max_upload_bytes: int = 8_000_000
    llm_timeout_seconds: float = 45.0
    llm_retries: int = 1

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        level = (value or "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return level

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def binds_publicly(self) -> bool:
        return self.app_host not in {"127.0.0.1", "localhost", "::1"}


@lru_cache
def settings() -> Settings:
    return Settings()


class ConfidenceBands(BaseModel):
    high: float = Field(default=0.75, ge=0.0, le=1.0)
    medium: float = Field(default=0.5, ge=0.0, le=1.0)


class ActionPolicy(BaseModel):
    advance_min_must_have_score: int = Field(default=2, ge=0, le=3)
    advance_min_overall: float = Field(default=0.62, ge=0.0, le=1.0)
    hold_min_overall: float = Field(default=0.45, ge=0.0, le=1.0)


class AppConfig(BaseModel):
    app_name: str = "Packet Review OS"
    version: str = "1.1.0"
    default_role_id: str = "fullstack_engineer"
    min_packet_chars: int = Field(default=80, ge=1)
    max_packet_chars: int = Field(default=40_000, ge=100)
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_retries: int = Field(default=1, ge=0, le=5)
    human_approval_required: bool = True
    retain_packets_in_db: bool = True
    redact_emails_in_logs: bool = True
    confidence: ConfidenceBands = Field(default_factory=ConfidenceBands)
    action_policy: ActionPolicy = Field(default_factory=ActionPolicy)

    @field_validator("confidence")
    @classmethod
    def _bands_ordered(cls, value: ConfidenceBands) -> ConfidenceBands:
        if value.medium > value.high:
            raise ValueError("confidence.medium must not be greater than confidence.high")
        return value

    def model_post_init(self, _context: Any) -> None:
        if self.action_policy.hold_min_overall > self.action_policy.advance_min_overall:
            raise ValueError(
                "action_policy.hold_min_overall must not exceed action_policy.advance_min_overall"
            )
        if self.min_packet_chars >= self.max_packet_chars:
            raise ValueError("min_packet_chars must be smaller than max_packet_chars")


class Criterion(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    must_have: bool = False
    weight: float = Field(default=1.0, gt=0)
    indicators: list[str] = Field(default_factory=list)
    negative_indicators: list[str] = Field(default_factory=list)


class Knockout(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    indicators: list[str] = Field(default_factory=list)
    action: str = "reject_with_reason"

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        if value not in VALID_ACTIONS:
            raise ValueError(f"action {value!r} is not one of {sorted(VALID_ACTIONS)}")
        return value


class RiskFlagConfig(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    indicators: list[str] = Field(default_factory=list)


class RoleConfig(BaseModel):
    role_id: str = Field(min_length=1)
    title: str = ""
    company: str = ""
    summary: str = ""
    criteria: list[Criterion] = Field(min_length=1)
    knockouts: list[Knockout] = Field(default_factory=list)
    risk_flags: list[RiskFlagConfig] = Field(default_factory=list)

    @field_validator("criteria")
    @classmethod
    def _unique_ids(cls, value: list[Criterion]) -> list[Criterion]:
        ids = [c.id for c in value]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate criterion id(s): {sorted(duplicates)}")
        if not any(c.must_have for c in value):
            raise ValueError("at least one criterion must be marked must_have: true")
        return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML. {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a YAML mapping at the top level.")
    return data


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path.name} has {exc.error_count()} configuration problem(s):"]
    for err in exc.errors():
        where = ".".join(str(part) for part in err["loc"]) or "(root)"
        lines.append(f"  - {where}: {err['msg']}")
    return "\n".join(lines)


@lru_cache
def app_config() -> AppConfig:
    path = ROOT / "config" / "app.yaml"
    try:
        return AppConfig.model_validate(load_yaml(path))
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def roles_dir() -> Path:
    return ROOT / "config" / "roles"


@lru_cache
def load_role(role_id: str) -> RoleConfig:
    """Load and validate one role scorecard. Raises ConfigError with a readable message."""
    safe_id = (role_id or "").strip()
    if not safe_id or "/" in safe_id or "\\" in safe_id or safe_id.startswith("."):
        raise ConfigError(f"Invalid role id {role_id!r}.")
    path = roles_dir() / f"{safe_id}.yaml"
    if not path.exists():
        available = ", ".join(r["id"] for r in list_roles()) or "none found"
        raise ConfigError(
            f"Unknown role {safe_id!r}. Expected {path.name} in config/roles/. Available: {available}."
        )
    data = load_yaml(path)
    declared = str(data.get("role_id") or "").strip()
    if declared and declared != safe_id:
        # The filename is the identity used in URLs and stored reviews. The most
        # likely way to reach here is copying an existing scorecard to a new
        # filename and forgetting to change role_id inside, which would
        # otherwise register the role under an id that cannot be loaded back.
        raise ConfigError(
            f"{path.name} declares role_id: {declared!r} but the file is named {safe_id!r}. "
            f"Set role_id: {safe_id} inside the file, or rename the file to {declared}.yaml."
        )
    data["role_id"] = safe_id
    try:
        return RoleConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def list_roles() -> list[dict[str, str]]:
    """List roles for the UI. A broken file is listed with its error, not hidden."""
    roles: list[dict[str, str]] = []
    for path in sorted(roles_dir().glob("*.yaml")):
        try:
            role = load_role(path.stem)
        except ConfigError as exc:
            logger.error("Skipping role file %s: %s", path.name, exc)
            roles.append({"id": path.stem, "title": f"{path.stem} (misconfigured)", "company": "", "error": str(exc)})
            continue
        roles.append({"id": role.role_id, "title": role.title or role.role_id, "company": role.company, "error": ""})
    return roles


def validate_all() -> list[str]:
    """Validate every config file. Returns a list of human-readable problems."""
    problems: list[str] = []
    try:
        app_config()
    except ConfigError as exc:
        problems.append(str(exc))
    role_files = sorted(roles_dir().glob("*.yaml"))
    if not role_files:
        problems.append(f"No role scorecards found in {roles_dir()}. Add at least one .yaml file.")
    for path in role_files:
        try:
            load_role(path.stem)
        except ConfigError as exc:
            problems.append(str(exc))
    return problems
