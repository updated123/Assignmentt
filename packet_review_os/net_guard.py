"""URL safety checks for operator-supplied job-description links.

The job-URL field is the one place a user hands us an arbitrary address that the
server then fetches. Without a guard that is a server-side request forgery (SSRF)
hole: an operator could be socially engineered into pasting
``http://169.254.169.254/latest/meta-data/`` or ``http://127.0.0.1:8000/history``
and the app would happily fetch internal resources and paste them into a review.

Policy: allow only http/https to public unicast addresses. Private, loopback,
link-local, multicast and reserved ranges are refused unless the caller opts in
explicitly (``allow_private=True``), which only the evaluation fixtures do.

Residual risk (documented, not fixed): we validate the resolved address and then
issue the request by hostname, so a DNS entry that changes between the two steps
(DNS rebinding) is not defeated. Closing that requires pinning the connection to
the validated IP. Accepted for a loopback-bound operator tool; see
docs/RUNBOOK.md before exposing this app on a network.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames that resolve to infrastructure metadata services on common clouds.
BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)


class UnsafeUrlError(ValueError):
    """Raised when a URL must not be fetched by the server."""


def _address_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return False
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    # IPv4-mapped and 6to4 style addresses can smuggle a private v4 target.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and not _address_is_public(mapped):
        return False
    sixtofour = getattr(ip, "sixtofour", None)
    return sixtofour is None or _address_is_public(sixtofour)


def resolve_host(host: str) -> list[str]:
    """Resolve a hostname to every address it maps to. Raises UnsafeUrlError on failure."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve host {host!r}. Check the URL and your network.") from exc
    return sorted({info[4][0] for info in infos})


def assert_url_is_fetchable(url: str, *, allow_private: bool = False) -> str:
    """Validate a job URL before the server fetches it.

    Returns the normalized URL. Raises UnsafeUrlError with an operator-readable
    message when the URL must not be fetched.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise UnsafeUrlError("Job URL is empty.")

    parts = urlsplit(candidate)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrlError("Job URL must start with http:// or https://")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeUrlError("Job URL is missing a hostname.")
    if allow_private:
        return candidate
    if host in BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(
            f"Refusing to fetch {host!r}: that is a cloud metadata endpoint, not a job posting."
        )

    # A literal IP in the URL is checked directly; a name is checked after resolution.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    addresses = [str(literal)] if literal is not None else resolve_host(host)
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise UnsafeUrlError(f"Could not interpret address {raw!r} for host {host!r}.") from None
        if not _address_is_public(ip):
            raise UnsafeUrlError(
                f"Refusing to fetch {host!r}: it points at the private/internal address {raw}. "
                "Paste the job description text instead."
            )
    return candidate
