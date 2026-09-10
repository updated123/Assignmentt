from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


class PdfExtractError(ValueError):
    pass


def extract_pdf_text(data: bytes, filename: str = "upload.pdf") -> str:
    if not data:
        raise PdfExtractError("The PDF file was empty.")
    if len(data) > 8_000_000:
        raise PdfExtractError("PDF is larger than 8 MB. Paste the relevant pages as text instead.")
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise PdfExtractError(f"Could not read {filename}. Upload a text-based PDF or paste the resume.") from exc
    if getattr(reader, "is_encrypted", False):
        raise PdfExtractError("This PDF is encrypted. Export an unencrypted copy or paste the text.")
    pages = []
    for page in reader.pages[:12]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n".join(pages).strip()
    if len(text) < 40:
        raise PdfExtractError(
            "No readable text found in the PDF. It may be a scan. Paste the resume text instead."
        )
    return text
