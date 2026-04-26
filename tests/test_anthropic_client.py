"""Tests for empire.api.anthropic_client.

Note: the autouse `block_live_api_hosts` fixture from conftest blocks any
live POST to api.anthropic.com. Tests here mock at the API boundary by
patching `empire.api.anthropic_client._post_once`.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from empire.api import anthropic_client
from empire.api.anthropic_client import _enforce_no_opus, post_messages
from empire.exceptions import (
    MissingTelemetryContext,
    OpusModelBlocked,
)


def _mock_response(status_code: int, json_data: dict | None = None,
                   text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or (str(json_data) if json_data else "")
    if status_code >= 400:
        # Make raise_for_status actually raise
        def _raise():
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}", request=MagicMock(), response=resp,
            )
        resp.raise_for_status.side_effect = _raise
    else:
        resp.raise_for_status.return_value = None
    return resp


# --- _enforce_no_opus ---


def test_enforce_no_opus_blocks_opus():
    with pytest.raises(OpusModelBlocked):
        _enforce_no_opus("claude-opus-4-6")


def test_enforce_no_opus_blocks_case_insensitive():
    with pytest.raises(OpusModelBlocked):
        _enforce_no_opus("Claude-OPUS-4-6")


def test_enforce_no_opus_allows_sonnet():
    _enforce_no_opus("claude-sonnet-4-6")  # no raise


def test_enforce_no_opus_allows_haiku():
    _enforce_no_opus("claude-haiku-4-5-20251001")  # no raise


def test_enforce_no_opus_allows_empty():
    # Empty / None → falls through (model-resolution error caught elsewhere)
    _enforce_no_opus("")


# --- post_messages: hard-block paths ---


def test_post_messages_requires_app(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    with pytest.raises(MissingTelemetryContext):
        post_messages(app="", action="x", messages=[{"role": "user", "content": "hi"}])


def test_post_messages_requires_action(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    with pytest.raises(MissingTelemetryContext):
        post_messages(app="kbk", action="", messages=[{"role": "user", "content": "hi"}])


def test_post_messages_blocks_opus_before_network(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")
    with pytest.raises(OpusModelBlocked):
        post_messages(
            app="kbk",
            action="test",
            model="claude-opus-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )


def test_post_messages_raises_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        post_messages(
            app="kbk",
            action="test",
            messages=[{"role": "user", "content": "hi"}],
        )


# --- post_messages: happy path ---


def test_post_messages_success_logs_usage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

    fake_resp = _mock_response(200, {
        "id": "msg_1",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "hello"}],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    })

    log_calls: list = []

    def fake_log(**kwargs):
        log_calls.append(kwargs)

    with patch.object(anthropic_client, "_post_once", return_value=fake_resp), \
         patch.object(anthropic_client, "log_usage", side_effect=fake_log):
        result = post_messages(
            app="kbk",
            action="daily_brief",
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert result["id"] == "msg_1"
    assert len(log_calls) == 1
    assert log_calls[0]["app"] == "kbk"
    assert log_calls[0]["action"] == "daily_brief"
    assert log_calls[0]["input_tokens"] == 10
    assert log_calls[0]["output_tokens"] == 20


# --- post_messages: retry logic ---


def test_post_messages_retries_on_429(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

    responses = [
        _mock_response(429),
        _mock_response(200, {
            "id": "msg_2",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }),
    ]
    call_count = {"n": 0}

    def fake_post_once(**kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return responses[idx]

    with patch.object(anthropic_client, "_post_once", side_effect=fake_post_once), \
         patch.object(anthropic_client, "time") as fake_time, \
         patch.object(anthropic_client, "log_usage"):
        fake_time.sleep = lambda s: None
        result = post_messages(
            app="kbk", action="t",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert result["id"] == "msg_2"
    assert call_count["n"] == 2


def test_post_messages_retries_on_500(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

    responses = [
        _mock_response(503),
        _mock_response(503),
        _mock_response(200, {
            "id": "msg_3",
            "model": "claude-sonnet-4-6",
            "content": [],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }),
    ]
    it = iter(responses)

    with patch.object(anthropic_client, "_post_once",
                      side_effect=lambda **kw: next(it)), \
         patch.object(anthropic_client, "time") as fake_time, \
         patch.object(anthropic_client, "log_usage"):
        fake_time.sleep = lambda s: None
        result = post_messages(
            app="kbk", action="t",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert result["id"] == "msg_3"


def test_post_messages_does_not_retry_on_400(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

    responses = [_mock_response(400, text="bad request")]
    call_count = {"n": 0}

    def fake_post_once(**kwargs):
        call_count["n"] += 1
        return responses[0]

    with patch.object(anthropic_client, "_post_once", side_effect=fake_post_once):
        with pytest.raises(httpx.HTTPStatusError):
            post_messages(
                app="kbk", action="t",
                messages=[{"role": "user", "content": "hi"}],
            )
    assert call_count["n"] == 1


def test_post_messages_default_model_is_sonnet(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

    captured: dict = {}

    def fake_post_once(*, api_key, payload, timeout):
        captured["payload"] = payload
        return _mock_response(200, {
            "id": "msg",
            "model": payload["model"],
            "content": [],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    with patch.object(anthropic_client, "_post_once", side_effect=fake_post_once), \
         patch.object(anthropic_client, "log_usage"):
        post_messages(
            app="kbk", action="t",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert captured["payload"]["model"] == "claude-sonnet-4-6"
