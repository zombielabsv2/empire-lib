"""Usage logger — appends a row to Supabase api_usage_log per Claude call.

Fail-silent on Supabase outage. This is the ONE exception to the empire's
hard-block rule: accounting must never block the main API call. If
Supabase is unreachable, we log a warning to stderr and return.
"""
from __future__ import annotations

import json
import sys

import httpx

from empire.config.supabase_creds import get_supabase_creds
from empire.exceptions import SupabaseCredsNotFound

# Anthropic pricing per token (USD), Apr 2026.
_ANTHROPIC_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-sonnet-4-20250514": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-opus-4-6": {"input": 15.0 / 1_000_000, "output": 75.0 / 1_000_000},
    "claude-haiku-4-5-20251001": {"input": 0.25 / 1_000_000, "output": 1.25 / 1_000_000},
}
_DEFAULT_PRICING = {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute Anthropic call cost in USD using the empire pricing table."""
    pricing = _ANTHROPIC_PRICING.get(model, _DEFAULT_PRICING)
    cost = (input_tokens * pricing["input"]) + (output_tokens * pricing["output"])
    return round(cost, 6)


def log_usage(
    *,
    app: str,
    action: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Append a row to Supabase api_usage_log. Fails silent on outage.

    All args after `*` are required kwargs. If `cost_usd` is None it is
    computed from the empire pricing table. If Supabase is unreachable the
    function logs a warning to stderr and returns — never raises, never
    blocks the calling Claude flow.
    """
    if cost_usd is None:
        cost_usd = estimate_cost_usd(model, input_tokens, output_tokens)
    try:
        url, key = get_supabase_creds()
    except SupabaseCredsNotFound as e:
        print(f"[empire.usage_logger] supabase creds not found, skipping log: {e}",
              file=sys.stderr)
        return
    row = {
        "app": app,
        "action": action,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(float(cost_usd), 6),
        "user_id": user_id,
        "metadata": json.dumps(metadata) if metadata else None,
    }
    try:
        httpx.post(
            f"{url}/rest/v1/api_usage_log",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=row,
            timeout=5.0,
        )
    except Exception as e:
        print(f"[empire.usage_logger] log failed (non-fatal): "
              f"{type(e).__name__}: {e}", file=sys.stderr)
