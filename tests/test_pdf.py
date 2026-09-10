"""PDF handling.

Resumes arrive as PDFs, so this is a real input path, not a nicety. The
guarantees are: extract text when there is text, refuse clearly when there is
not, and never let a large or malformed file take the process down.
"""

from __future__ import annotations

import logging

import pytest

from packet_review_os.pdf_extract import PdfExtractError, extract_pdf_text
from packet_review_os.pipeline import run_review
from packet_review_os.schemas import PacketInput
from scripts.make_sample_pdf import build_pdf  # noqa: E402


def test_generated_sample_pdf_parses_without_warnings(caplog):
    """The shipped sample is read on every demo; it must not log xref repairs."""
    with caplog.at_level(logging.WARNING, logger="pypdf"):
        text = extract_pdf_text(build_pdf(), "priya_nair.pdf")
    assert "Priya Nair" in text
    assert "FastAPI" in text
    assert caplog.records == [], f"pypdf logged: {[r.getMessage() for r in caplog.records]}"


def test_empty_file_is_refused():
    with pytest.raises(PdfExtractError, match="empty"):
        extract_pdf_text(b"", "empty.pdf")


def test_non_pdf_bytes_are_refused_with_guidance():
    with pytest.raises(PdfExtractError) as exc:
        extract_pdf_text(b"this is a text file, not a pdf", "notes.txt")
    assert "notes.txt" in str(exc.value)
    assert "paste" in str(exc.value).lower()


def test_oversized_pdf_is_refused_before_parsing():
    with pytest.raises(PdfExtractError, match="8 MB"):
        extract_pdf_text(b"%PDF-1.4" + b"0" * 9_000_000, "big.pdf")


def test_text_free_pdf_is_reported_as_a_probable_scan():
    """A page with no text operators is the scanned-resume case."""
    pdf = build_pdf().replace(b"(Priya Nair) Tj", b"")
    with pytest.raises(PdfExtractError, match="scan|readable text"):
        extract_pdf_text(pdf.replace(b") Tj", b")"), "scan.pdf")


def test_pdf_text_flows_into_a_full_review():
    result = run_review(
        PacketInput(role_id="fullstack_engineer", packet_text=""),
        pdf_text=extract_pdf_text(build_pdf(), "priya_nair.pdf"),
    )
    assert result.pdf_used is True
    assert result.candidate_name == "Priya Nair"
    assert any("PDF" in line for line in result.logs)
