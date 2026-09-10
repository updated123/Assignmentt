from __future__ import annotations

import hashlib
import re

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\-\s().]{7,}\d")
WHITESPACE_RE = re.compile(r"\s+")


def collapse_ws(text: str) -> str:
    return WHITESPACE_RE.sub(" ", (text or "").strip())


def normalize_for_match(text: str) -> str:
    return collapse_ws(text).lower()


def redact_pii(text: str) -> str:
    redacted = EMAIL_RE.sub("[email redacted]", text or "")
    redacted = PHONE_RE.sub("[phone redacted]", redacted)
    return redacted


def find_quote(source: str, needle: str, window: int = 140) -> str:
    if not source or not needle:
        return ""
    idx = normalize_for_match(source).find(normalize_for_match(needle))
    if idx < 0:
        return ""
    start = max(0, idx - 20)
    end = min(len(source), idx + len(needle) + window)
    quote = collapse_ws(source[start:end])
    if start > 0:
        quote = "…" + quote
    if end < len(source):
        quote = quote + "…"
    return quote[:280]


def quote_in_source(quote: str, source: str) -> bool:
    if not quote:
        return False
    cleaned = collapse_ws(quote).strip("…").strip()
    if len(cleaned) < 8:
        return False
    return normalize_for_match(cleaned) in normalize_for_match(source)


def first_matching_window(source: str, terms: list[str], radius: int = 90) -> str:
    lowered = normalize_for_match(source)
    original = collapse_ws(source)
    original_lower = original.lower()
    for term in terms:
        t = term.lower().strip()
        if not t:
            continue
        idx = original_lower.find(t)
        if idx == -1:
            idx = lowered.find(t)
            if idx == -1:
                continue
        start = max(0, idx - 30)
        end = min(len(original), idx + len(t) + radius)
        snippet = original[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(original):
            snippet = snippet + "…"
        return snippet[:280]
    return ""


def extract_name_guess(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    skip_prefixes = ("forward", "subject", "to:", "cc:", "date:", "---", ">", "from:")
    for line in lines[:20]:
        low = line.lower()
        if low.startswith(("name:", "candidate:")):
            return re.sub(r"^(name|candidate)\s*:\s*", "", line, flags=re.I).split("<")[0].strip()[:80]
        if low.startswith(skip_prefixes):
            continue
        if "@" in line or len(line) > 48:
            continue
        words = line.split()
        if (
            2 <= len(words) <= 4
            and line[0].isupper()
            and not any(
                w.lower()
                in {
                    "resume",
                    "curriculum",
                    "linkedin",
                    "forwarded",
                    "internal",
                    "recruiter",
                    "full",
                    "stack",
                    "software",
                    "engineer",
                    "engineering",
                    "backend",
                    "frontend",
                }
                for w in words
            )
        ):
            return line[:80]
    return ""


def years_mentioned(text: str) -> float | None:
    patterns = [
        r"(\d+(?:\.\d+)?)\s*\+?\s*years",
        r"(\d+(?:\.\d+)?)\s*\+?\s*yrs",
        r"experience[:\s]+(\d+(?:\.\d+)?)\s*\+?\s*years",
    ]
    hits = []
    for pat in patterns:
        for match in re.finditer(pat, text or "", flags=re.I):
            hits.append(float(match.group(1)))
    return max(hits) if hits else None


def too_short(text: str, min_chars: int) -> bool:
    return len(collapse_ws(text)) < min_chars


def sha16(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
