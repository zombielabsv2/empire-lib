"""Tests for empire.test.guards.

These tests verify the guard works AGAINST the autouse fixture loaded by
conftest. Calling httpx.post to a banned host should raise LiveAPIBlocked.
"""
from __future__ import annotations

import httpx
import pytest

from empire.exceptions import LiveAPIBlocked
from empire.test.guards import _check_url


def test_check_url_blocks_anthropic():
    with pytest.raises(LiveAPIBlocked) as exc_info:
        _check_url("https://api.anthropic.com/v1/messages")
    assert exc_info.value.host == "api.anthropic.com"


def test_check_url_blocks_resend():
    with pytest.raises(LiveAPIBlocked):
        _check_url("https://api.resend.com/emails")


def test_check_url_blocks_facebook_graph():
    with pytest.raises(LiveAPIBlocked):
        _check_url("https://graph.facebook.com/v19.0/act_123/insights")


def test_check_url_blocks_google_ads():
    with pytest.raises(LiveAPIBlocked):
        _check_url("https://googleads.googleapis.com/v15/customers/123/searchStream")


def test_check_url_allows_supabase():
    _check_url("https://abc.supabase.co/rest/v1/api_usage_log")  # no raise


def test_check_url_allows_localhost():
    _check_url("http://localhost:8000/test")
    _check_url("http://127.0.0.1:8080/api")


def test_check_url_case_insensitive_host():
    with pytest.raises(LiveAPIBlocked):
        _check_url("https://API.Anthropic.COM/v1/messages")


def test_autouse_fixture_blocks_real_httpx_post():
    """The conftest activates the guard — direct httpx.post must raise."""
    with pytest.raises(LiveAPIBlocked):
        httpx.post("https://api.anthropic.com/v1/messages", json={})


def test_autouse_fixture_blocks_resend_post():
    with pytest.raises(LiveAPIBlocked):
        httpx.post("https://api.resend.com/emails", json={})
