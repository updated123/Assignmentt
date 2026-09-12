"""The interface a non-developer actually touches.

Covers the happy path, the three ways a reviewer can give bad input, the
approval loop, and the access control that switches on when the app is bound to
something other than loopback.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from packet_review_os import storage
from packet_review_os import web as web_mod


@pytest.fixture
def client(temp_db):
    with TestClient(web_mod.app) as test_client:
        yield test_client


@pytest.fixture
def strong_packet(packet_text):
    return packet_text("TC01")


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_home_renders_the_form(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Run scorecard review" in response.text


def test_health_is_public_and_reveals_no_candidate_data(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["config_problems"] == 0
    assert "roles" not in body


def test_paste_a_packet_and_get_a_grounded_scorecard(client, strong_packet):
    response = client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": strong_packet})
    assert response.status_code == 200
    assert "Priya Nair" in response.text
    assert "Advance to screen" in response.text
    assert "Shipped Python FastAPI services" in response.text, "the evidence quote should be visible"


def test_review_is_persisted_and_retrievable(client, strong_packet):
    client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": strong_packet})
    records = storage.list_reviews()
    assert len(records) == 1
    detail = client.get(f"/review/{records[0].run_id}")
    assert detail.status_code == 200
    assert records[0].candidate_name in detail.text


def test_history_lists_stored_reviews(client, strong_packet):
    client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": strong_packet})
    response = client.get("/history")
    assert response.status_code == 200
    assert "Priya Nair" in response.text


def test_approval_is_recorded(client, strong_packet):
    client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": strong_packet})
    run_id = storage.list_reviews()[0].run_id
    response = client.post(
        f"/review/{run_id}/decision",
        data={"approved": "approve", "note": "booking the screen"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert storage.get_review(run_id)["approved"] is True


def test_send_back_is_recorded_as_not_approved(client, strong_packet):
    client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": strong_packet})
    run_id = storage.list_reviews()[0].run_id
    client.post(f"/review/{run_id}/decision", data={"approved": "send_back"}, follow_redirects=True)
    assert storage.get_review(run_id)["approved"] is False


# --------------------------------------------------------------------------- #
# Bad input gets a readable page, never a traceback
# --------------------------------------------------------------------------- #


def test_empty_submission_is_refused_with_guidance(client):
    response = client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": "   "})
    assert response.status_code == 400
    assert "upload a PDF" in response.text


def test_non_pdf_upload_is_refused_with_guidance(client):
    response = client.post(
        "/review",
        data={"role_id": "fullstack_engineer", "packet_text": ""},
        files={"pdf": ("resume.pdf", io.BytesIO(b"definitely not a pdf"), "application/pdf")},
    )
    assert response.status_code == 400
    assert "text-based PDF" in response.text or "paste" in response.text.lower()


def test_oversized_upload_is_refused_before_parsing(client, monkeypatch, make_settings):
    monkeypatch.setattr(web_mod, "settings", lambda: make_settings(max_upload_bytes=1000))
    big = io.BytesIO(b"%PDF-1.4" + b"0" * 50_000)
    response = client.post(
        "/review",
        data={"role_id": "fullstack_engineer", "packet_text": ""},
        files={"pdf": ("huge.pdf", big, "application/pdf")},
    )
    assert response.status_code == 400
    assert "larger than" in response.text


def test_unknown_run_id_renders_a_404_page_with_a_hint(client):
    response = client.get("/review/does-not-exist")
    assert response.status_code == 404
    assert "History" in response.text


def test_invalid_decision_value_is_rejected(client, strong_packet):
    client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": strong_packet})
    run_id = storage.list_reviews()[0].run_id
    response = client.post(f"/review/{run_id}/decision", data={"approved": "delete_candidate"})
    assert response.status_code == 400


def test_unknown_role_renders_a_configuration_page(client, strong_packet):
    response = client.post("/review", data={"role_id": "not_a_role", "packet_text": strong_packet})
    assert response.status_code == 500
    assert "not_a_role" in response.text
    assert "check" in response.text


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #


def test_api_returns_the_structured_result(client, strong_packet):
    response = client.post(
        "/api/review",
        json={"role_id": "fullstack_engineer", "packet_text": strong_packet},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["next_action"] == "advance_to_screen"
    assert body["human_must_approve"] is True
    assert body["draft_email"]["send_allowed"] is False
    assert body["criteria"], "the API should expose the per-criterion evidence"


def test_api_reports_a_bad_role_as_json_not_html(client, strong_packet):
    response = client.post("/api/review", json={"role_id": "nope", "packet_text": strong_packet})
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_api_rejects_a_malformed_body(client):
    assert client.post("/api/review", json={"packet_text": "missing role"}).status_code == 422


# --------------------------------------------------------------------------- #
# Bundled examples, so a first-time reviewer never has to copy a file by hand
# --------------------------------------------------------------------------- #


def test_form_offers_the_bundled_examples(client):
    body = client.get("/").text
    assert "Load example" in body
    assert "TC01_strong_match" in body


def test_an_example_loads_in_full(client):
    payload = client.get("/samples/TC01_strong_match").json()
    assert payload["name"] == "TC01_strong_match"
    assert "Priya Nair" in payload["text"]
    # The failure this endpoint exists to prevent: a packet short enough to be
    # rejected as empty rather than scored.
    assert len(payload["text"]) > 800


def test_a_loaded_example_scores_as_a_real_packet(client):
    text = client.get("/samples/TC01_strong_match").json()["text"]
    response = client.post("/review", data={"role_id": "fullstack_engineer", "packet_text": text})
    assert "Advance to screen" in response.text


def test_unknown_example_is_a_clean_404(client):
    assert client.get("/samples/nope").status_code == 404


@pytest.mark.parametrize("name", ["../../.env", "..%2f..%2f.env", "../config/app"])
def test_examples_cannot_walk_out_of_the_samples_directory(client, name):
    assert client.get(f"/samples/{name}").status_code in {307, 404}


# --------------------------------------------------------------------------- #
# Access control, which matters only when the app is not on loopback
# --------------------------------------------------------------------------- #


def test_no_token_required_on_a_loopback_deployment(client):
    assert client.get("/").status_code == 200


def test_token_is_required_once_configured(temp_db, monkeypatch, make_settings):
    monkeypatch.setattr(web_mod, "settings", lambda: make_settings(api_token="s3cret", app_host="0.0.0.0"))
    with TestClient(web_mod.app) as guarded:
        assert guarded.get("/").status_code == 401
        assert guarded.get("/history").status_code == 401
        assert guarded.get("/", headers={"X-API-Token": "s3cret"}).status_code == 200
        assert guarded.get("/", headers={"X-API-Token": "wrong"}).status_code == 401
        # Health stays open so an operator can check liveness without the token.
        assert guarded.get("/health").status_code == 200


def test_token_in_the_url_is_remembered_in_a_cookie(temp_db, monkeypatch, make_settings):
    """The reviewer pastes the link once; later navigation just works."""
    monkeypatch.setattr(web_mod, "settings", lambda: make_settings(api_token="s3cret", app_host="0.0.0.0"))
    with TestClient(web_mod.app) as guarded:
        first = guarded.get("/?token=s3cret")
        assert first.status_code == 200
        assert web_mod.TOKEN_COOKIE in first.cookies
        assert guarded.get("/history").status_code == 200


def test_401_page_explains_how_to_get_in(temp_db, monkeypatch, make_settings):
    monkeypatch.setattr(web_mod, "settings", lambda: make_settings(api_token="s3cret", app_host="0.0.0.0"))
    with TestClient(web_mod.app) as guarded:
        assert "token=" in guarded.get("/").text
