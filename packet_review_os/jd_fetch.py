from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

import httpx

from .net_guard import UnsafeUrlError, assert_url_is_fetchable

logger = logging.getLogger("packet_review_os.jd_fetch")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
MIN_READABLE_CHARS = 80
MAX_TEXT_CHARS = 12_000


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"script", "style", "noscript", "template"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


class JobFetchError(ValueError):
    pass


def _read_capped(response: httpx.Response) -> str:
    """Read at most MAX_BYTES so a huge or endless page cannot exhaust memory."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_BYTES:
            logger.warning("Job page exceeded %d bytes; truncating.", MAX_BYTES)
            chunks.append(chunk[: MAX_BYTES - (total - len(chunk))])
            break
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


def html_to_text(html: str) -> str:
    parser = _VisibleText()
    try:
        parser.feed(html)
        text = " ".join(parser.parts)
    except Exception:  # noqa: BLE001 - malformed markup falls back to a regex strip
        logger.warning("HTML parse failed; falling back to tag stripping.")
        text = TAG_RE.sub(" ", html)
    return WS_RE.sub(" ", text).strip()


def fetch_job_text(url: str, timeout: float = 12.0, *, allow_private: bool = False) -> str:
    """Fetch a job posting and return readable text.

    Every hop (the original URL and each redirect target) is re-validated against
    the SSRF policy, because a public URL is allowed to redirect to a private one.
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        current = assert_url_is_fetchable(url, allow_private=allow_private)
    except UnsafeUrlError as exc:
        raise JobFetchError(str(exc)) from exc

    headers = {
        "User-Agent": "PacketReviewOS/1.1 (hiring-eval; local operator tool)",
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
            for hop in range(MAX_REDIRECTS + 1):
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise JobFetchError("Job URL redirected without a destination.")
                        if hop >= MAX_REDIRECTS:
                            raise JobFetchError(f"Job URL redirected more than {MAX_REDIRECTS} times.")
                        target = str(httpx.URL(current).join(location))
                        try:
                            current = assert_url_is_fetchable(target, allow_private=allow_private)
                        except UnsafeUrlError as exc:
                            raise JobFetchError(f"Redirect blocked. {exc}") from exc
                        logger.info("Job URL redirected to %s", current)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if "pdf" in content_type:
                        raise JobFetchError("That URL is a PDF. Upload the file instead of fetching it.")
                    if content_type and not any(
                        allowed in content_type for allowed in ("text/html", "text/plain", "xhtml", "text/")
                    ):
                        raise JobFetchError(
                            f"Job URL returned {content_type.split(';')[0]!r}, not a readable web page."
                        )
                    body = _read_capped(response)
                    break
            else:  # pragma: no cover - loop always breaks or raises
                raise JobFetchError("Job URL could not be resolved to a page.")
    except httpx.HTTPStatusError as exc:
        raise JobFetchError(
            f"Job URL returned HTTP {exc.response.status_code}. Check the link or paste the text instead."
        ) from exc
    except httpx.HTTPError as exc:
        raise JobFetchError(f"Could not fetch job URL: {exc}") from exc

    text = html_to_text(body)
    if len(text) < MIN_READABLE_CHARS:
        raise JobFetchError("Fetched page did not contain enough readable job text.")
    return text[:MAX_TEXT_CHARS]
