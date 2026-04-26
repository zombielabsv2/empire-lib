"""Anthropic /v1/messages client — hard-block on Opus, hard-block on missing telemetry.

Public function: post_messages(*, app, action, model, messages, ...)

Empire rules enforced here:
- Opus model id (case-insensitive substring) raises OpusModelBlocked.
- Missing app/action raises MissingTelemetryContext (catches at call site,
  not later in cost report).
- Every 200-OK call auto-logs to api_usage_log via empire.api.usage_logger.

Retries: 3 attempts on 429 / 5xx with exponential backoff (1s, 2s, 4s).
Default timeout: 60s read.
Default model: claude-sonnet-4-6.
"""
from __future__ import annotations

import os
import time

import httpx

from empire.api.usage_logger import log_usage
from empire.exceptions import (
    MissingTelemetryContext,
    OpusModelBlocked,
)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0)
MAX_RETRIES = 3


def _enforce_no_opus(model: str) -> None:
    """Raise OpusModelBlocked if `model` contains 'opus' (case-insensitive).

    Empire-wide rule: feedback_no_opus_on_api.md.
    """
    if "opus" in (model or "").lower():
        raise OpusModelBlocked(
            f"Opus is banned empire-wide on the Anthropic API "
            f"(feedback_no_opus_on_api.md). Got model={model!r}. "
            f"Use a Sonnet or Haiku model instead."
        )


def _resolve_api_key() -> str:
    """Return ANTHROPIC_API_KEY from env. Empty string if not set."""
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _post_once(
    *,
    api_key: str,
    payload: dict,
    timeout: httpx.Timeout,
) -> httpx.Response:
    return httpx.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "content-type": "application/json",
            "anthropic-version": API_VERSION,
        },
        json=payload,
        timeout=timeout,
    )


def post_messages(
    *,
    app: str,
    action: str,
    model: str = DEFAULT_MODEL,
    messages: list,
    max_tokens: int = 1024,
    system: str | None = None,
    timeout: httpx.Timeout | None = None,
    metadata: dict | None = None,
) -> dict:
    """POST to Anthropic /v1/messages with hard-block guardrails.

    All keyword-only. Required: app, action, messages. Default model is Sonnet.

    Returns the parsed JSON dict from a 200-OK response. Raises:
    - MissingTelemetryContext if app or action is empty.
    - OpusModelBlocked if model id contains 'opus'.
    - httpx.HTTPStatusError on non-200 after retries.
    - RuntimeError if ANTHROPIC_API_KEY is unset.
    """
    if not app or not action:
        raise MissingTelemetryContext(
            "post_messages requires non-empty app and action kwargs "
            "(see feedback memo: every Claude call must be attributable in "
            "api_usage_log)."
        )
    _enforce_no_opus(model)

    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in the environment. "
            "Set it before calling post_messages."
        )

    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system is not None:
        payload["system"] = system

    if timeout is None:
        timeout = DEFAULT_TIMEOUT

    last_resp: httpx.Response | None = None
    for attempt in range(MAX_RETRIES):
        resp = _post_once(api_key=api_key, payload=payload, timeout=timeout)
        last_resp = resp
        if resp.status_code == 200:
            data = resp.json()
            usage = data.get("usage", {}) or {}
            in_tok = int(usage.get("input_tokens", 0))
            out_tok = int(usage.get("output_tokens", 0))
            resp_model = data.get("model") or model
            try:
                log_usage(
                    app=app,
                    action=action,
                    model=resp_model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    metadata=metadata,
                )
            except Exception:
                # log_usage already fail-silent, but belt-and-suspenders.
                pass
            return data

        # Retry only on 429 / 5xx
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt < MAX_RETRIES - 1:
                backoff = 2 ** attempt  # 1, 2, 4
                time.sleep(backoff)
                continue

        # Non-retryable, or last attempt — raise.
        resp.raise_for_status()

    # Defensive: shouldn't reach here.
    if last_resp is not None:
        last_resp.raise_for_status()
    raise RuntimeError("post_messages exhausted retries with no response")
