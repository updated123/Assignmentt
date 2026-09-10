"""Shared fixtures.

Two things need care in this codebase:

* ``settings()``, ``app_config()`` and ``load_role()`` are ``lru_cache``d, which
  is right for a long-lived server and wrong for tests that change
  configuration. ``reset_config_caches`` clears them around every test.
* ``storage`` resolves its database path from settings at call time and
  remembers which database it has initialized. ``temp_db`` points it at a
  throwaway file so tests never touch ``data/packet_review.db``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packet_review_os import config as config_mod  # noqa: E402
from packet_review_os import storage as storage_mod  # noqa: E402
from packet_review_os.config import Settings  # noqa: E402

SAMPLES = ROOT / "samples" / "packets"


def _clear_caches() -> None:
    config_mod.settings.cache_clear()
    config_mod.app_config.cache_clear()
    config_mod.load_role.cache_clear()


@pytest.fixture(autouse=True)
def reset_config_caches():
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
def make_settings():
    """Build a Settings object without reading the developer's real .env."""

    def _make(**overrides) -> Settings:
        base: dict = {
            "openai_api_key": "",
            "app_host": "127.0.0.1",
            "api_token": "",
            "allow_private_job_urls": False,
            "_env_file": None,
        }
        base.update(overrides)
        return Settings(**base)

    return _make


@pytest.fixture
def temp_db(tmp_path, monkeypatch, make_settings):
    """Point the store at a throwaway SQLite file."""
    db_path = tmp_path / "reviews.db"
    stub = make_settings(database_path=str(db_path))
    monkeypatch.setattr(storage_mod, "settings", lambda: stub)
    monkeypatch.setattr(storage_mod, "_initialized_for", None, raising=False)
    storage_mod.init_db(force=True)
    yield db_path
    monkeypatch.setattr(storage_mod, "_initialized_for", None, raising=False)


@pytest.fixture
def packet_text():
    def _load(case_id: str) -> str:
        matches = sorted(SAMPLES.glob(f"{case_id}*.txt"))
        if not matches:
            raise AssertionError(f"No sample packet matching {case_id!r}")
        return matches[0].read_text(encoding="utf-8")

    return _load


@pytest.fixture
def role():
    return config_mod.load_role("fullstack_engineer")
