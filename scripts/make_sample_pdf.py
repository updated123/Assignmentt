"""Create the sample text PDF used by the demo and the PDF integration fixture.

Written by hand so the repository needs no PDF library, but the byte offsets in
the cross-reference table are *computed* rather than hard-coded. The v1.0 version
hard-coded them and then rewrote every newline to CRLF, which shifted every
offset: the file still opened, because pypdf rebuilds a broken xref, but it
logged "incorrect startxref pointer" on every read. A sample artifact should not
teach the reader to ignore warnings.

Run:  python scripts/make_sample_pdf.py
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples" / "resumes" / "priya_nair.pdf"

LINES = [
    "Priya Nair",
    "5 years of experience as a software engineer",
    "Python FastAPI React TypeScript PostgreSQL pytest AWS",
    "Shipped a B2B SaaS billing dashboard with a product manager and designer.",
    "Code review on every pull request. Docker. GitHub Actions.",
]


def _escape(text: str) -> str:
    """Escape the characters that terminate a PDF string literal."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream() -> bytes:
    parts = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for index, line in enumerate(LINES):
        if index:
            parts.append("T*")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("ascii")


def build_pdf() -> bytes:
    stream = _content_stream()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return bytes(out)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build_pdf())
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
