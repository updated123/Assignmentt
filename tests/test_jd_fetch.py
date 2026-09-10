"""Job-URL fetching.

The important case is the second one: a public URL is allowed to redirect, and a
redirect target is a fresh SSRF decision. Checking only the URL the operator
typed would let ``https://bit.ly/x`` reach ``http://169.254.169.254/``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from packet_review_os import net_guard
from packet_review_os.jd_fetch import (
    MAX_BYTES,
    JobFetchError,
    fetch_job_text,
    html_to_text,
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Resolve test hostnames to a public address.

    The SSRF guard resolves before fetching, so without this every test here
    would fail at DNS rather than exercising the fetch logic. The guard's own
    resolution behaviour is covered in test_net_guard.py.
    """
    monkeypatch.setattr(net_guard, "resolve_host", lambda host: ["93.184.216.34"])


JOB_HTML = """
<html><head><title>Job</title><style>.x{color:red}</style></head>
<body>
  <script>console.log("ignore me")</script>
  <h1>Mid-level Full-Stack Engineer</h1>
  <p>You will ship Python FastAPI services and a React/TypeScript UI for a B2B SaaS product.</p>
  <p>We expect three or more years of professional experience and production ownership.</p>
</body></html>
"""


@respx.mock
def test_public_job_page_is_fetched_and_reduced_to_text():
    respx.get("https://jobs.example.com/fullstack").mock(
        return_value=httpx.Response(200, html=JOB_HTML)
    )
    text = fetch_job_text("https://jobs.example.com/fullstack")
    assert "Python FastAPI" in text
    assert "console.log" not in text, "script contents must not enter the packet"
    assert "color:red" not in text, "style contents must not enter the packet"


@respx.mock
def test_redirect_to_a_private_address_is_blocked():
    respx.get("https://jobs.example.com/r").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    )
    with pytest.raises(JobFetchError) as exc:
        fetch_job_text("https://jobs.example.com/r")
    assert "redirect" in str(exc.value).lower()


@respx.mock
def test_redirect_to_another_public_page_is_followed():
    respx.get("https://jobs.example.com/r").mock(
        return_value=httpx.Response(301, headers={"location": "https://jobs.example.com/final"})
    )
    respx.get("https://jobs.example.com/final").mock(return_value=httpx.Response(200, html=JOB_HTML))
    assert "Python FastAPI" in fetch_job_text("https://jobs.example.com/r")


@respx.mock
def test_a_redirect_loop_is_bounded():
    respx.get("https://jobs.example.com/loop").mock(
        return_value=httpx.Response(302, headers={"location": "https://jobs.example.com/loop"})
    )
    with pytest.raises(JobFetchError, match="redirected more than"):
        fetch_job_text("https://jobs.example.com/loop")


@respx.mock
def test_redirect_without_a_location_is_reported():
    respx.get("https://jobs.example.com/r").mock(return_value=httpx.Response(302))
    with pytest.raises(JobFetchError, match="without a destination"):
        fetch_job_text("https://jobs.example.com/r")


@respx.mock
def test_http_error_status_is_reported_readably():
    respx.get("https://jobs.example.com/gone").mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(JobFetchError) as exc:
        fetch_job_text("https://jobs.example.com/gone")
    assert "404" in str(exc.value)
    assert "paste" in str(exc.value).lower()


@respx.mock
def test_pdf_url_tells_the_operator_to_upload_instead():
    respx.get("https://jobs.example.com/jd.pdf").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")
    )
    with pytest.raises(JobFetchError, match="Upload the file"):
        fetch_job_text("https://jobs.example.com/jd.pdf")


@respx.mock
def test_non_page_content_type_is_refused():
    respx.get("https://jobs.example.com/data").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK")
    )
    with pytest.raises(JobFetchError, match="not a readable web page"):
        fetch_job_text("https://jobs.example.com/data")


@respx.mock
def test_oversized_page_is_truncated_rather_than_buffered_whole():
    huge = "<html><body>" + ("Python FastAPI React engineer. " * 200_000) + "</body></html>"
    respx.get("https://jobs.example.com/huge").mock(return_value=httpx.Response(200, html=huge))
    text = fetch_job_text("https://jobs.example.com/huge")
    assert len(text) <= 12_000, "the extracted text is capped before it reaches the packet"
    assert len(huge.encode()) > MAX_BYTES


@respx.mock
def test_page_with_no_readable_text_is_refused():
    respx.get("https://jobs.example.com/empty").mock(
        return_value=httpx.Response(200, html="<html><body><div></div></body></html>")
    )
    with pytest.raises(JobFetchError, match="readable job text"):
        fetch_job_text("https://jobs.example.com/empty")


@respx.mock
def test_network_failure_is_reported_not_raised_raw():
    respx.get("https://jobs.example.com/down").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(JobFetchError, match="Could not fetch"):
        fetch_job_text("https://jobs.example.com/down")


def test_empty_url_is_a_no_op():
    assert fetch_job_text("") == ""
    assert fetch_job_text("   ") == ""


def test_private_url_is_blocked_before_any_request_is_made():
    """A literal private IP needs no DNS, so the stub above cannot mask it.

    No respx mock here: if a request were attempted, the test would error out.
    """
    with pytest.raises(JobFetchError):
        fetch_job_text("http://127.0.0.1:1/jd")


def test_html_to_text_survives_malformed_markup():
    assert "Engineer" in html_to_text("<p>Engineer<<<>>> <div unclosed")
