"""Persist GoldenResult rows to Supabase claude_golden_runs.

Schema (apply via supabase/migrations/...sql; see this module's docstring
for the migration text):

    create table if not exists claude_golden_runs (
        id          uuid primary key default gen_random_uuid(),
        app         text not null,
        action      text not null,
        passed      boolean not null,
        duration_s  numeric not null,
        ran_at      timestamptz not null,
        failures    jsonb,                  -- list of failure strings
        output      jsonb,                  -- raw prompt_fn return value
        exception   text                    -- traceback if prompt_fn raised
    );
    create index if not exists idx_golden_runs_app_action_ran_at
        on claude_golden_runs (app, action, ran_at desc);

Best-effort: if Supabase creds are not resolvable, the store call logs
and returns False rather than raising. The cron's exit code is driven by
the test pass/fail, not by storage success — losing one run's row is
acceptable; failing the cron over a transient Supabase blip is not.
"""
from __future__ import annotations

import json
import os
import sys

import httpx

from empire.config.supabase_creds import get_supabase_creds
from empire.exceptions import SupabaseCredsNotFound

TABLE = "claude_golden_runs"


def store_run(result, *, table: str = TABLE) -> bool:
    """Insert one row into claude_golden_runs. Returns True on success."""
    try:
        url, key = get_supabase_creds()
    except SupabaseCredsNotFound as e:
        print(f"[empire.eval.store] supabase creds not found: {e}", file=sys.stderr)
        return False

    # Output blob can be huge (full HTML email). Cap at 50 KB to keep rows lean
    # and avoid silently exceeding row-size limits. Truncation is recorded
    # explicitly so a future drift analysis can re-run if it needs full content.
    output_serialized = result.output
    truncated = False
    try:
        text = json.dumps(output_serialized, default=str)
        if len(text) > 50_000:
            output_serialized = {
                "_truncated": True,
                "_original_len": len(text),
                "_preview": text[:5_000],
            }
            truncated = True
    except Exception:
        output_serialized = {"_unserializable": str(type(result.output).__name__)}

    row = {
        "app": result.spec.app,
        "action": result.spec.action,
        "passed": result.passed,
        "duration_s": round(result.duration_s, 3),
        "ran_at": result.ran_at,
        "failures": result.failures,
        "output": output_serialized,
        "exception": result.exception,
    }
    if truncated:
        row["failures"] = list(row["failures"]) + ["[note] output truncated for storage"]

    try:
        resp = httpx.post(
            f"{url}/rest/v1/{table}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=row,
            timeout=10.0,
        )
    except Exception as e:
        print(f"[empire.eval.store] insert failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False

    if resp.status_code >= 300:
        print(
            f"[empire.eval.store] http {resp.status_code}: {resp.text[:200]}",
            file=sys.stderr,
        )
        return False
    return True
