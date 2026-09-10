"""SSRF policy for operator-supplied job URLs.

The job-URL field is the only place a user hands the server an address to fetch.
v1.0 fetched anything, including cloud metadata endpoints and the app's own
loopback port. These tests are the regression fence for that.
"""

from __future__ import annotations

import pytest

from packet_review_os import net_guard
from packet_review_os.net_guard import UnsafeUrlError, assert_url_is_fetchable

BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/Azure metadata
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://127.0.0.1:8000/history",  # the app's own stored reviews
    "http://localhost/admin",
    "http://10.1.2.3/jd",
    "http://192.168.0.10/jd",
    "http://172.16.5.4/jd",
    "http://[::1]/jd",
    "http://0.0.0.0/jd",
]

REJECTED_SCHEMES = [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/jd",
    "",
    "   ",
    "not-a-url",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_private_and_metadata_targets_are_refused(url):
    with pytest.raises(UnsafeUrlError):
        assert_url_is_fetchable(url)


@pytest.mark.parametrize("url", REJECTED_SCHEMES)
def test_non_http_schemes_are_refused(url):
    with pytest.raises(UnsafeUrlError):
        assert_url_is_fetchable(url)


def test_public_url_is_allowed(monkeypatch):
    """Resolution is stubbed so the verdict, not the network, is under test."""
    monkeypatch.setattr(net_guard, "resolve_host", lambda host: ["93.184.216.34"])
    assert assert_url_is_fetchable("https://example.com/jobs/123") == "https://example.com/jobs/123"


def test_a_host_resolving_to_any_private_address_is_refused(monkeypatch):
    """DNS returning both a public and a private address must still be refused."""
    monkeypatch.setattr(net_guard, "resolve_host", lambda host: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(UnsafeUrlError):
        assert_url_is_fetchable("https://split-horizon.example/jd")


def test_allow_private_opt_in_is_required_and_sufficient():
    """The evaluation fixture needs loopback; nothing else should get it."""
    with pytest.raises(UnsafeUrlError):
        assert_url_is_fetchable("http://127.0.0.1:9/x")
    assert assert_url_is_fetchable("http://127.0.0.1:9/x", allow_private=True)


def test_ipv4_mapped_ipv6_cannot_smuggle_a_private_target():
    with pytest.raises(UnsafeUrlError):
        assert_url_is_fetchable("http://[::ffff:127.0.0.1]/jd")


def test_unresolvable_host_is_refused_with_a_readable_message():
    with pytest.raises(UnsafeUrlError) as exc:
        assert_url_is_fetchable("http://this-host-does-not-exist.invalid/jd")
    assert "resolve" in str(exc.value).lower()


def test_trailing_dot_hostname_is_still_checked():
    """'localhost.' resolves the same as 'localhost' and must not slip through."""
    with pytest.raises(UnsafeUrlError):
        assert_url_is_fetchable("http://localhost./jd")
