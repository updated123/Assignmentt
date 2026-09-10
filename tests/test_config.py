"""Configuration validation.

The stated extension path for this system is a non-developer editing a role
scorecard in ``config/roles/*.yaml``. So a typo has to produce a message that
names the file and the field, not a traceback from inside the scorer on the
first real packet.
"""

from __future__ import annotations

import pytest
import yaml

from packet_review_os import config as config_mod
from packet_review_os.config import (
    AppConfig,
    ConfigError,
    Knockout,
    RoleConfig,
    load_role,
    validate_all,
)

MINIMAL_ROLE = {
    "title": "Temp Role",
    "criteria": [
        {"id": "python_backend", "label": "Python", "must_have": True, "indicators": ["python"]},
    ],
}


@pytest.fixture
def role_file(tmp_path, monkeypatch):
    """Write a role YAML into a throwaway roles directory."""
    roles = tmp_path / "roles"
    roles.mkdir()
    monkeypatch.setattr(config_mod, "roles_dir", lambda: roles)

    def _write(name: str, data) -> None:
        text = data if isinstance(data, str) else yaml.safe_dump(data)
        (roles / f"{name}.yaml").write_text(text, encoding="utf-8")
        config_mod.load_role.cache_clear()

    return _write


# --------------------------------------------------------------------------- #
# The shipped configuration must be valid.
# --------------------------------------------------------------------------- #


def test_shipped_configuration_is_valid():
    assert validate_all() == []


def test_shipped_role_has_must_haves_and_knockouts():
    role = load_role("fullstack_engineer")
    assert any(c.must_have for c in role.criteria)
    assert {k.id for k in role.knockouts} >= {"hostile_conduct", "wrong_discipline"}


# --------------------------------------------------------------------------- #
# Role scorecard errors
# --------------------------------------------------------------------------- #


def test_unknown_role_lists_what_is_available(role_file):
    role_file("temp_role", MINIMAL_ROLE)
    with pytest.raises(ConfigError) as exc:
        load_role("account_executive")
    message = str(exc.value)
    assert "account_executive" in message
    assert "temp_role" in message, "the error should tell the operator what does exist"


def test_filename_and_declared_role_id_must_agree(role_file):
    """Copying a scorecard to a new filename without editing role_id is the likely slip."""
    role_file("account_executive", {**MINIMAL_ROLE, "role_id": "fullstack_engineer"})
    with pytest.raises(ConfigError) as exc:
        load_role("account_executive")
    assert "role_id" in str(exc.value)
    assert "account_executive" in str(exc.value)


def test_matching_role_id_is_accepted(role_file):
    role_file("account_executive", {**MINIMAL_ROLE, "role_id": "account_executive"})
    assert load_role("account_executive").role_id == "account_executive"


def test_role_with_no_must_have_is_rejected(role_file):
    data = {**MINIMAL_ROLE, "criteria": [{"id": "nice", "label": "Nice", "must_have": False}]}
    role_file("temp_role", data)
    with pytest.raises(ConfigError, match="must_have"):
        load_role("temp_role")


def test_duplicate_criterion_ids_are_rejected(role_file):
    data = {
        **MINIMAL_ROLE,
        "criteria": [
            {"id": "dup", "label": "One", "must_have": True},
            {"id": "dup", "label": "Two"},
        ],
    }
    role_file("temp_role", data)
    with pytest.raises(ConfigError, match="duplicate"):
        load_role("temp_role")


def test_criterion_missing_a_label_names_the_field(role_file):
    data = {**MINIMAL_ROLE, "criteria": [{"id": "python_backend", "must_have": True}]}
    role_file("temp_role", data)
    with pytest.raises(ConfigError) as exc:
        load_role("temp_role")
    assert "label" in str(exc.value)
    assert "temp_role.yaml" in str(exc.value)


def test_unknown_knockout_action_is_rejected():
    with pytest.raises(ValueError, match="not one of"):
        Knockout(id="k", label="K", action="delete_candidate")


def test_negative_weight_is_rejected(role_file):
    data = {
        **MINIMAL_ROLE,
        "criteria": [{"id": "python_backend", "label": "Python", "must_have": True, "weight": -2}],
    }
    role_file("temp_role", data)
    with pytest.raises(ConfigError):
        load_role("temp_role")


def test_broken_yaml_is_reported_as_a_config_error(role_file):
    role_file("temp_role", "criteria: [oops\n  bad: indentation")
    with pytest.raises(ConfigError, match="valid YAML"):
        load_role("temp_role")


def test_empty_role_file_is_rejected(role_file):
    role_file("temp_role", "")
    with pytest.raises(ConfigError):
        load_role("temp_role")


@pytest.mark.parametrize("bad_id", ["../secrets", "a/b", "", ".hidden", "a\\b"])
def test_role_id_cannot_escape_the_roles_directory(bad_id):
    with pytest.raises(ConfigError):
        load_role(bad_id)


def test_validate_all_reports_every_broken_file(role_file):
    role_file("good_role", MINIMAL_ROLE)
    role_file("bad_role", {"criteria": []})
    problems = validate_all()
    assert any("bad_role" in p for p in problems)
    assert not any("good_role" in p for p in problems)


def test_list_roles_surfaces_a_broken_file_instead_of_hiding_it(role_file):
    role_file("good_role", MINIMAL_ROLE)
    role_file("bad_role", {"criteria": []})
    listed = {r["id"]: r for r in config_mod.list_roles()}
    assert listed["bad_role"]["error"], "a misconfigured role must be visible in the UI"
    assert "misconfigured" in listed["bad_role"]["title"]
    assert not listed["good_role"]["error"]


def test_missing_roles_directory_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "roles_dir", lambda: tmp_path / "absent")
    problems = validate_all()
    assert any("No role scorecards" in p for p in problems)


# --------------------------------------------------------------------------- #
# App-level policy thresholds
# --------------------------------------------------------------------------- #


def test_confidence_bands_must_be_ordered():
    with pytest.raises(ValueError, match="medium"):
        AppConfig.model_validate({"confidence": {"high": 0.4, "medium": 0.9}})


def test_hold_threshold_cannot_exceed_advance_threshold():
    with pytest.raises(ValueError, match="hold_min_overall"):
        AppConfig.model_validate(
            {"action_policy": {"advance_min_overall": 0.4, "hold_min_overall": 0.8}}
        )


def test_packet_length_bounds_must_be_ordered():
    with pytest.raises(ValueError, match="min_packet_chars"):
        AppConfig.model_validate({"min_packet_chars": 5000, "max_packet_chars": 100})


def test_out_of_range_threshold_is_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"action_policy": {"advance_min_overall": 7.5}})


def test_unknown_log_level_falls_back_to_info(make_settings):
    assert make_settings(log_level="chatty").log_level == "INFO"


def test_public_bind_is_detected(make_settings):
    assert make_settings(app_host="0.0.0.0").binds_publicly is True
    assert make_settings(app_host="127.0.0.1").binds_publicly is False


def test_role_config_accepts_a_full_scorecard():
    role = RoleConfig.model_validate({**MINIMAL_ROLE, "role_id": "temp_role"})
    assert role.criteria[0].weight == 1.0, "weight should default rather than be required"
