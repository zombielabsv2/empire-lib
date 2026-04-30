"""Lightweight JSON shape validator.

Why this exists: every cron and route in the empire that consumes Claude's
JSON output is one bad parse away from silent corruption. The current
pattern is `json.loads(text)` followed by `data["body_html"]` — if the
model dropped a key, you get KeyError at use-site, often deep inside an
HTML render, often as a Cloud Run job that exits 0 because the exception
was swallowed by a try/except higher up.

This module turns "I expect this shape" into a runtime check that fails
loudly at the boundary, with a precise path to the offending field.

Shape DSL (intentionally tiny — one screen of code, no extra deps):

    {
        "key": str,                 # required string
        "key2?": str,               # optional (suffix `?`)
        "items": [str],             # list of strings
        "nested": {"sub": int},     # nested dict with this shape
        "many": [{"k": str}],       # list of dicts with this shape
        "either": (str, int),       # tuple = type union (any of these)
    }

For richer needs (oneOf, regex constraints, length limits) reach for
jsonschema / pydantic — this module is for the 80% case that Claude
output validators actually need: "did the model emit the keys I asked
for, with roughly the right types".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ShapeViolation:
    path: str  # dotted JSON path, e.g. "actions[0].title"
    expected: str
    got: str

    def __str__(self) -> str:
        return f"{self.path}: expected {self.expected}, got {self.got}"


def _typename(t: Any) -> str:
    if isinstance(t, type):
        return t.__name__
    if isinstance(t, tuple):
        return " | ".join(_typename(x) for x in t)
    return type(t).__name__


def _check_type(obj: Any, expected: Any, path: str) -> list[ShapeViolation]:
    """obj must be an instance of `expected` (a type or tuple of types)."""
    if isinstance(expected, tuple) and all(isinstance(t, type) for t in expected):
        if not isinstance(obj, expected):
            return [ShapeViolation(path, _typename(expected), _typename(obj))]
        # bool is a subclass of int in Python — disallow accidental bool-as-int
        if int in expected and isinstance(obj, bool) and bool not in expected:
            return [ShapeViolation(path, _typename(expected), "bool")]
        return []
    if isinstance(expected, type):
        if not isinstance(obj, expected):
            return [ShapeViolation(path, expected.__name__, _typename(obj))]
        if expected is int and isinstance(obj, bool):
            return [ShapeViolation(path, "int", "bool")]
        return []
    return [ShapeViolation(path, "<type>", f"unparseable expected {expected!r}")]


def validate_shape(obj: Any, shape: Any, path: str = "") -> list[ShapeViolation]:
    """Walk `obj` against `shape`, return all violations (empty list = valid).

    Validates:
    - top-level dict: each key in shape (minus optional `?` markers) must be
      present in obj with the right type
    - list shape `[item_shape]`: obj must be a list, each element validated
      against item_shape
    - nested dict shape: recurses with path prefix
    - extra keys in obj (not in shape) are allowed silently — the model
      adding optional fields is fine; the model dropping required ones is not
    """
    # List shape: [item_shape]
    if isinstance(shape, list):
        if len(shape) != 1:
            return [ShapeViolation(path, "list[<one shape>]", f"shape with {len(shape)} elements")]
        if not isinstance(obj, list):
            return [ShapeViolation(path, "list", _typename(obj))]
        violations: list[ShapeViolation] = []
        item_shape = shape[0]
        for i, item in enumerate(obj):
            item_path = f"{path}[{i}]"
            violations.extend(validate_shape(item, item_shape, item_path))
        return violations

    # Dict shape: {"key": sub_shape, ...}
    if isinstance(shape, dict):
        if not isinstance(obj, dict):
            return [ShapeViolation(path or "<root>", "dict", _typename(obj))]
        violations = []
        for key, sub_shape in shape.items():
            optional = key.endswith("?")
            actual_key = key[:-1] if optional else key
            sub_path = f"{path}.{actual_key}" if path else actual_key
            if actual_key not in obj:
                if optional:
                    continue
                violations.append(
                    ShapeViolation(sub_path, _shape_summary(sub_shape), "<missing>")
                )
                continue
            violations.extend(validate_shape(obj[actual_key], sub_shape, sub_path))
        return violations

    # Type or type-union
    return _check_type(obj, shape, path or "<root>")


def _shape_summary(shape: Any) -> str:
    """One-line description of a shape (for missing-key error messages)."""
    if isinstance(shape, dict):
        return "dict"
    if isinstance(shape, list) and len(shape) == 1:
        return f"list[{_shape_summary(shape[0])}]"
    return _typename(shape)


def assert_shape(obj: Any, shape: Any, *, source: str = "claude_response") -> Any:
    """Validate `obj` against `shape`; raise ValueError with a clean report on failure.

    Returns `obj` unchanged on success so this can be used inline:

        digest = assert_shape(json.loads(text), DIGEST_SHAPE, source="weekly_email")

    The `source` tag goes into the error so logs identify which boundary failed.
    """
    violations = validate_shape(obj, shape)
    if not violations:
        return obj
    lines = [f"shape mismatch in {source} ({len(violations)} violation(s)):"]
    for v in violations:
        lines.append(f"  - {v}")
    raise ValueError("\n".join(lines))
