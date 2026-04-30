"""Tests for empire.eval.runner.

The runner orchestrates: call prompt_fn, validate shape, check substring
assertions, return a GoldenResult. Every test here uses a synthetic
prompt_fn (no live Anthropic call) so the test suite stays fast and
hermetic.
"""
from __future__ import annotations

from empire.eval import GoldenSpec, run_golden


def test_passing_spec_with_shape_and_substring():
    def prompt_fn(name: str):
        return {"greeting": f"hello {name}", "count": 1}

    spec = GoldenSpec(
        app="test",
        action="greet",
        prompt_fn=prompt_fn,
        prompt_args=("world",),
        expected_shape={"greeting": str, "count": int},
        must_contain=["hello", "world"],
    )
    result = run_golden(spec)
    assert result.passed is True
    assert result.failures == []
    assert result.output == {"greeting": "hello world", "count": 1}
    assert result.duration_s >= 0


def test_failing_shape_check():
    def prompt_fn():
        return {"greeting": "hi", "count": "not-an-int"}

    spec = GoldenSpec(
        app="test",
        action="greet",
        prompt_fn=prompt_fn,
        expected_shape={"greeting": str, "count": int},
    )
    result = run_golden(spec)
    assert result.passed is False
    assert any("count" in f for f in result.failures)


def test_missing_required_substring():
    def prompt_fn():
        return {"text": "completely different content"}

    spec = GoldenSpec(
        app="test",
        action="t",
        prompt_fn=prompt_fn,
        must_contain=["expected_phrase"],
    )
    result = run_golden(spec)
    assert result.passed is False
    assert any("expected_phrase" in f for f in result.failures)


def test_banned_substring_present():
    def prompt_fn():
        return {"copy": "Block-printed in Sanganer"}

    spec = GoldenSpec(
        app="test",
        action="t",
        prompt_fn=prompt_fn,
        must_not_contain=["Sanganer"],
    )
    result = run_golden(spec)
    assert result.passed is False
    assert any("Sanganer" in f for f in result.failures)


def test_substring_check_walks_nested_structures():
    """must_contain/must_not_contain should find strings deep in nested dicts/lists."""

    def prompt_fn():
        return {"actions": [{"title": "deep value"}]}

    spec = GoldenSpec(
        app="test",
        action="t",
        prompt_fn=prompt_fn,
        must_contain=["deep value"],
    )
    result = run_golden(spec)
    assert result.passed is True


def test_prompt_fn_raising_exception():
    def prompt_fn():
        raise RuntimeError("upstream timeout")

    spec = GoldenSpec(app="test", action="t", prompt_fn=prompt_fn)
    result = run_golden(spec)
    assert result.passed is False
    assert result.output is None
    assert result.exception is not None
    assert "upstream timeout" in result.exception


def test_prompt_fn_returning_none():
    def prompt_fn():
        return None

    spec = GoldenSpec(app="test", action="t", prompt_fn=prompt_fn)
    result = run_golden(spec)
    assert result.passed is False
    assert any("None" in f for f in result.failures)


def test_label_property():
    spec = GoldenSpec(app="kairav_os", action="weekly_email", prompt_fn=lambda: None)
    assert spec.label == "kairav_os/weekly_email"


def test_args_and_kwargs_passed_through():
    captured = {}

    def prompt_fn(a, b, c=None):
        captured["a"] = a
        captured["b"] = b
        captured["c"] = c
        return {"ok": True}

    spec = GoldenSpec(
        app="test",
        action="t",
        prompt_fn=prompt_fn,
        prompt_args=(1, 2),
        prompt_kwargs={"c": 3},
    )
    run_golden(spec)
    assert captured == {"a": 1, "b": 2, "c": 3}
