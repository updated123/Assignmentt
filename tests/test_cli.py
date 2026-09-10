"""The command line, which is how an operator checks and runs the system.

Exit codes are part of the contract: 0 fine, 1 runtime problem, 2 configuration
problem. A wrapper script or CI job depends on that distinction.
"""

from __future__ import annotations

import json

import pytest

from packet_review_os import __main__ as cli
from packet_review_os import config as config_mod

SAMPLE = "samples/packets/TC01_strong_match.txt"


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #


def test_check_passes_on_the_shipped_configuration(capsys):
    assert cli.main(["check"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Configuration OK" in out
    assert "fullstack_engineer" in out


def test_check_reports_a_broken_scorecard_with_exit_code_2(tmp_path, monkeypatch, capsys):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "broken.yaml").write_text("criteria: []\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "roles_dir", lambda: roles)
    config_mod.load_role.cache_clear()

    assert cli.main(["check"]) == cli.EXIT_CONFIG
    assert "broken.yaml" in capsys.readouterr().err


def test_check_warns_when_bound_publicly_without_a_token(monkeypatch, capsys, make_settings):
    monkeypatch.setattr(cli, "settings", lambda: make_settings(app_host="0.0.0.0", api_token=""))
    assert cli.main(["check"]) == cli.EXIT_OK
    assert "WARNING" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# roles
# --------------------------------------------------------------------------- #


def test_roles_lists_the_configured_scorecards(capsys):
    assert cli.main(["roles"]) == cli.EXIT_OK
    assert "Mid-level Full-Stack Engineer" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# review
# --------------------------------------------------------------------------- #


def test_review_prints_a_decision_and_says_nothing_was_sent(temp_db, capsys):
    assert cli.main(["review", SAMPLE]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Priya Nair" in out
    assert "Advance to screen" in out
    assert "not sent" in out


def test_review_json_output_is_machine_readable(temp_db, capsys):
    assert cli.main(["review", SAMPLE, "--json"]) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_action"] == "advance_to_screen"
    assert payload["draft_email"]["send_allowed"] is False
    assert payload["human_must_approve"] is True


def test_review_stores_the_run(temp_db):
    from packet_review_os import storage

    cli.main(["review", SAMPLE])
    assert storage.count_reviews() == 1


def test_review_of_a_missing_file_exits_1(capsys):
    assert cli.main(["review", "samples/packets/nope.txt"]) == cli.EXIT_RUNTIME
    assert "not found" in capsys.readouterr().err


def test_review_of_a_non_utf8_file_explains_the_problem(tmp_path, capsys):
    bad = tmp_path / "latin1.txt"
    bad.write_bytes("Renée Dupont, ingénieure".encode("latin-1"))
    assert cli.main(["review", str(bad)]) == cli.EXIT_RUNTIME
    assert "UTF-8" in capsys.readouterr().err


def test_review_of_a_broken_pdf_exits_1(tmp_path, capsys):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not really a pdf")
    assert cli.main(["review", str(bad)]) == cli.EXIT_RUNTIME
    assert "PDF" in capsys.readouterr().err


def test_review_of_the_sample_pdf_works(temp_db, capsys):
    assert cli.main(["review", "samples/resumes/priya_nair.pdf"]) == cli.EXIT_OK
    assert "Priya Nair" in capsys.readouterr().out


def test_review_with_an_unknown_role_exits_2_with_guidance(capsys):
    assert cli.main(["review", SAMPLE, "--role", "account_executive"]) == cli.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "account_executive" in err
    assert "check" in err


def test_review_notes_are_applied(temp_db, capsys):
    """A referral supplied on the command line must reach the policy."""
    assert cli.main(["review", SAMPLE, "--notes", "Referred by our CEO."]) == cli.EXIT_OK
    assert "Escalate" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# serve and eval
# --------------------------------------------------------------------------- #


def test_serve_refuses_to_start_on_invalid_configuration(tmp_path, monkeypatch, capsys):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "broken.yaml").write_text("criteria: []\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "roles_dir", lambda: roles)
    config_mod.load_role.cache_clear()

    started = {"called": False}
    monkeypatch.setitem(
        __import__("sys").modules,
        "uvicorn",
        type("uvicorn", (), {"run": lambda *a, **k: started.__setitem__("called", True)}),
    )
    assert cli.main(["serve"]) == cli.EXIT_CONFIG
    assert started["called"] is False, "the server must not start with a broken scorecard"
    assert "Refusing to start" in capsys.readouterr().err


def test_serve_honours_host_and_port_overrides(monkeypatch, capsys):
    captured = {}

    def fake_run(app, host, port, reload):
        captured.update({"app": app, "host": host, "port": port})

    monkeypatch.setitem(
        __import__("sys").modules, "uvicorn", type("uvicorn", (), {"run": staticmethod(fake_run)})
    )
    assert cli.main(["serve", "--host", "127.0.0.1", "--port", "9123"]) == cli.EXIT_OK
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9123


def test_eval_forwards_its_flags(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_cmd_eval", lambda args: seen.setdefault("args", args) or cli.EXIT_OK)
    cli.main(["eval", "--skip-llm-path", "--quiet"])
    assert seen["args"].skip_llm_path is True
    assert seen["args"].quiet is True


def test_unknown_command_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        cli.main(["frobnicate"])


def test_log_level_override_is_accepted(temp_db):
    assert cli.main(["--log-level", "ERROR", "review", SAMPLE]) == cli.EXIT_OK
