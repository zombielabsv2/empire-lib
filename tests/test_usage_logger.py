"""Tests for empire.api.usage_logger.

The logger must be fail-silent: Supabase outage / missing creds must NOT
raise, because logging is the one exception to the hard-block rule.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx

from empire.api import usage_logger
from empire.api.usage_logger import estimate_cost_usd, log_usage
from empire.config import supabase_creds


def test_estimate_cost_known_model():
    cost = estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000)
    # 3 + 15 = 18 USD per million combined
    assert cost == 18.0


def test_estimate_cost_unknown_model_uses_default():
    cost = estimate_cost_usd("some-future-model", 1_000_000, 0)
    assert cost == 3.0  # default sonnet input pricing


def test_log_usage_silent_when_no_creds(monkeypatch, capsys):
    """If creds are missing, log a stderr warning and return — must not raise."""
    supabase_creds.reset_cache()
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
              "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"):
        monkeypatch.delenv(k, raising=False)

    log_usage(
        app="kbk", action="t", model="claude-sonnet-4-6",
        input_tokens=10, output_tokens=20,
    )
    captured = capsys.readouterr()
    assert "supabase creds not found" in captured.err
    supabase_creds.reset_cache()


def test_log_usage_silent_on_httpx_error(monkeypatch, capsys):
    supabase_creds.reset_cache()
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k1")

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    with patch.object(usage_logger.httpx, "post", side_effect=boom):
        log_usage(
            app="kbk", action="t", model="claude-sonnet-4-6",
            input_tokens=1, output_tokens=1,
        )
    captured = capsys.readouterr()
    assert "log failed" in captured.err
    supabase_creds.reset_cache()


def test_log_usage_posts_to_correct_url(monkeypatch):
    supabase_creds.reset_cache()
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k1")

    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return None

    with patch.object(usage_logger.httpx, "post", side_effect=fake_post):
        log_usage(
            app="kbk",
            action="daily_brief",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=200,
            user_id="user_abc",
            metadata={"key": "value"},
        )

    assert captured["url"] == "https://abc.supabase.co/rest/v1/api_usage_log"
    row = captured["json"]
    assert row["app"] == "kbk"
    assert row["action"] == "daily_brief"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 200
    assert row["user_id"] == "user_abc"
    assert "value" in row["metadata"]
    assert row["cost_usd"] > 0
    supabase_creds.reset_cache()


def test_log_usage_explicit_cost_overrides_estimate(monkeypatch):
    supabase_creds.reset_cache()
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k1")

    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return None

    with patch.object(usage_logger.httpx, "post", side_effect=fake_post):
        log_usage(
            app="kbk", action="t", model="claude-sonnet-4-6",
            input_tokens=100, output_tokens=200, cost_usd=0.42,
        )
    assert captured["json"]["cost_usd"] == 0.42
    supabase_creds.reset_cache()
