"""Unit tests for empire.social.metricool — pure logic, no network calls."""
from __future__ import annotations

import pytest

from empire.social.metricool import (
    MetricoolAuthMissing,
    MetricoolClient,
    ScheduledPostResult,
)


def test_auth_missing_raises_when_env_absent(monkeypatch):
    monkeypatch.delenv("METRICOOL_USER_TOKEN", raising=False)
    monkeypatch.delenv("METRICOOL_USER_ID", raising=False)
    with pytest.raises(MetricoolAuthMissing):
        MetricoolClient()


def test_explicit_creds_ok():
    c = MetricoolClient(user_token="tok", user_id="123")
    assert c.user_token == "tok"
    assert c.user_id == "123"
    assert c._headers()["X-Mc-Auth"] == "tok"


def test_env_creds_picked_up(monkeypatch):
    monkeypatch.setenv("METRICOOL_USER_TOKEN", "envtok")
    monkeypatch.setenv("METRICOOL_USER_ID", "999")
    c = MetricoolClient()
    assert c.user_token == "envtok"
    assert c.user_id == "999"


def test_network_data_instagram_post():
    data = MetricoolClient._network_data(["instagram"], "post", True)
    assert data["instagramData"]["type"] == "POST"
    assert data["instagramData"]["autoPublish"] is True
    assert "showReelOnFeed" not in data["instagramData"]


def test_network_data_instagram_reel_shows_on_feed():
    data = MetricoolClient._network_data(["instagram"], "reel", True)
    assert data["instagramData"]["type"] == "REEL"
    assert data["instagramData"]["showReelOnFeed"] is True


def test_network_data_facebook_and_multi():
    data = MetricoolClient._network_data(["instagram", "facebook"], "post", False)
    assert data["facebookData"]["type"] == "POST"
    assert data["instagramData"]["autoPublish"] is False


def test_scheduled_post_result_defaults():
    r = ScheduledPostResult(ok=True, post_id="42")
    assert r.ok and r.post_id == "42"
    assert r.networks == [] and r.raw == {}


def test_guard_future_bumps_past_time():
    """A publish time in the past is bumped forward (the late-approval fix)."""
    from datetime import datetime, timedelta
    past = datetime(2020, 1, 1, 8, 8, 0)
    out = MetricoolClient._guard_future(past, "Asia/Kolkata")
    assert out > datetime.now() - timedelta(minutes=1)


def test_guard_future_preserves_future_time():
    from datetime import datetime, timedelta
    fut = datetime.now() + timedelta(days=3)
    out = MetricoolClient._guard_future(fut, "Asia/Kolkata")
    assert abs((out - fut).total_seconds()) < 1


def test_guard_future_bad_timezone_falls_back():
    from datetime import datetime, timedelta
    past = datetime(2020, 1, 1, 8, 8, 0)
    out = MetricoolClient._guard_future(past, "Not/AZone")
    assert out > datetime.now() - timedelta(minutes=1)
