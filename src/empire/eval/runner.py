"""Run a single golden specification through the live prompt function.

The runner deliberately invokes the project's actual prompt function
(not a re-implementation) so the golden tracks the production code path.
That means the function will hit api.anthropic.com — running goldens
costs ~$0.01-0.10 per spec depending on token volume.

Result rows include the raw output blob so subsequent runs can diff
text-level changes (semantic drift signals) without re-running the
expensive call.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from empire.lint.json_shape import validate_shape


@dataclass
class GoldenSpec:
    """Frozen specification for one prompt under regression test."""

    app: str  # which empire app (kairav_os, byrxj, astromedha, etc)
    action: str  # which prompt within the app (weekly_email, emi_report, etc)
    prompt_fn: Callable[..., Any]  # the live prompt function — call this with prompt_args
    prompt_args: tuple = ()
    prompt_kwargs: dict = field(default_factory=dict)
    expected_shape: Any = None  # passed to validate_shape; None to skip schema check
    must_contain: list[str] = field(default_factory=list)  # substrings expected somewhere in the output
    must_not_contain: list[str] = field(default_factory=list)  # substrings that must NOT appear

    @property
    def label(self) -> str:
        return f"{self.app}/{self.action}"


@dataclass
class GoldenResult:
    """Outcome of one golden run — persistable to claude_golden_runs."""

    spec: GoldenSpec
    passed: bool
    duration_s: float
    ran_at: str  # ISO timestamp
    output: Any  # the raw return value of prompt_fn (None on exception)
    failures: list[str] = field(default_factory=list)
    exception: str | None = None  # traceback if prompt_fn raised


def _flatten_strings(obj: Any) -> str:
    """Concat all string values found anywhere inside obj for substring checks."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_flatten_strings(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_flatten_strings(v) for v in obj)
    return ""


def run_golden(spec: GoldenSpec) -> GoldenResult:
    """Invoke the spec's prompt function and grade the output."""
    started = time.monotonic()
    ran_at = datetime.now(timezone.utc).isoformat()
    failures: list[str] = []
    output: Any = None
    exception_str: str | None = None

    try:
        output = spec.prompt_fn(*spec.prompt_args, **spec.prompt_kwargs)
    except Exception:
        exception_str = traceback.format_exc()
        failures.append(f"prompt_fn raised: {exception_str.splitlines()[-1]}")
        return GoldenResult(
            spec=spec,
            passed=False,
            duration_s=time.monotonic() - started,
            ran_at=ran_at,
            output=None,
            failures=failures,
            exception=exception_str,
        )

    if output is None:
        failures.append("prompt_fn returned None")

    # Schema check.
    if spec.expected_shape is not None and output is not None:
        violations = validate_shape(output, spec.expected_shape)
        if violations:
            for v in violations:
                failures.append(f"shape: {v}")

    # Substring assertions over the concatenated string content.
    flat = _flatten_strings(output)
    for needle in spec.must_contain:
        if needle not in flat:
            failures.append(f"missing required substring: {needle!r}")
    for banned in spec.must_not_contain:
        if banned in flat:
            failures.append(f"contains banned substring: {banned!r}")

    duration = time.monotonic() - started
    return GoldenResult(
        spec=spec,
        passed=not failures,
        duration_s=duration,
        ran_at=ran_at,
        output=output,
        failures=failures,
        exception=None,
    )
