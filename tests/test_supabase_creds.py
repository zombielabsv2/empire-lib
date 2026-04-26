"""Tests for empire.config.supabase_creds."""
from __future__ import annotations

import pytest

from empire.config import supabase_creds
from empire.config.supabase_creds import (
    diagnose_creds,
    get_supabase_creds,
    reset_cache,
)
from empire.exceptions import SupabaseCredsNotFound


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


def test_resolves_from_env_vars(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_test")
    url, key = get_supabase_creds()
    assert url == "https://abc.supabase.co"  # trailing slash stripped
    assert key == "sb_secret_test"


def test_falls_back_through_service_keys(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_KEY", "fallback_key")
    url, key = get_supabase_creds()
    assert key == "fallback_key"


def test_raises_when_nothing_resolves(monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
              "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SupabaseCredsNotFound):
        get_supabase_creds()


def test_negative_case_is_NOT_cached(monkeypatch):
    """The cold-start poisoning bug: negative result must be re-checked every call."""
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
              "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"):
        monkeypatch.delenv(k, raising=False)

    with pytest.raises(SupabaseCredsNotFound):
        get_supabase_creds()

    # Now creds become available — must resolve, not stay broken.
    monkeypatch.setenv("SUPABASE_URL", "https://late.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "late_key")

    url, key = get_supabase_creds()
    assert url == "https://late.supabase.co"
    assert key == "late_key"


def test_positive_case_IS_cached(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k1")
    url1, key1 = get_supabase_creds()

    # Even if env changes, cached value is returned (until reset_cache).
    monkeypatch.setenv("SUPABASE_URL", "https://other.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k2")
    url2, key2 = get_supabase_creds()
    assert (url1, key1) == (url2, key2)


def test_diagnose_creds_no_secret_value_leaked(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_supersecret_xyz")
    diag = diagnose_creds()
    assert diag["resolved"] is True
    assert "sb_secret_supersecret_xyz" not in str(diag)
    assert diag["key_len"] == len("sb_secret_supersecret_xyz")
    assert diag["key_prefix"].endswith("...")


def test_diagnose_creds_when_missing(monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
              "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"):
        monkeypatch.delenv(k, raising=False)
    diag = diagnose_creds()
    assert diag["resolved"] is False
