"""Tests for empire.lint.json_shape."""
from __future__ import annotations

import pytest

from empire.lint.json_shape import (
    ShapeViolation,
    assert_shape,
    validate_shape,
)


# ── Type checks ───────────────────────────────────────────────────────────


def test_str_passes():
    assert validate_shape("hello", str) == []


def test_str_fails_for_int():
    out = validate_shape(42, str)
    assert len(out) == 1
    assert out[0].expected == "str"
    assert out[0].got == "int"


def test_int_rejects_bool():
    """bool is a subclass of int in Python — must NOT be accepted as int."""
    out = validate_shape(True, int)
    assert len(out) == 1
    assert "bool" in out[0].got


def test_type_union_passes():
    assert validate_shape(42, (str, int)) == []
    assert validate_shape("x", (str, int)) == []


def test_type_union_fails():
    out = validate_shape(3.14, (str, int))
    assert len(out) == 1
    assert "float" in out[0].got


# ── Dict shape ────────────────────────────────────────────────────────────


def test_flat_dict_passes():
    shape = {"name": str, "age": int}
    assert validate_shape({"name": "Kairav", "age": 11}, shape) == []


def test_dict_missing_required_key():
    shape = {"name": str, "age": int}
    out = validate_shape({"name": "Kairav"}, shape)
    assert len(out) == 1
    assert out[0].path == "age"
    assert out[0].got == "<missing>"


def test_dict_optional_key_can_be_absent():
    shape = {"name": str, "age?": int}
    assert validate_shape({"name": "Kairav"}, shape) == []


def test_dict_optional_key_when_present_must_typecheck():
    shape = {"name": str, "age?": int}
    out = validate_shape({"name": "Kairav", "age": "eleven"}, shape)
    assert len(out) == 1
    assert out[0].path == "age"


def test_dict_extra_keys_allowed():
    """Model adding extra fields is fine; missing required is not."""
    shape = {"name": str}
    assert validate_shape({"name": "x", "extra": 99}, shape) == []


def test_root_must_be_dict():
    out = validate_shape("not a dict", {"name": str})
    assert len(out) == 1
    assert "dict" in out[0].expected


# ── Nested shapes ─────────────────────────────────────────────────────────


def test_nested_dict_validates():
    shape = {"user": {"name": str, "age": int}}
    assert validate_shape({"user": {"name": "K", "age": 11}}, shape) == []


def test_nested_dict_path_in_violation():
    shape = {"user": {"name": str, "age": int}}
    out = validate_shape({"user": {"name": "K", "age": "eleven"}}, shape)
    assert len(out) == 1
    assert out[0].path == "user.age"


def test_list_of_strings():
    assert validate_shape(["a", "b"], [str]) == []


def test_list_wrong_element_type():
    out = validate_shape(["a", 42, "c"], [str])
    assert len(out) == 1
    assert out[0].path == "[1]"


def test_list_of_dicts():
    shape = [{"title": str, "n": int}]
    obj = [{"title": "A", "n": 1}, {"title": "B", "n": 2}]
    assert validate_shape(obj, shape) == []


def test_list_of_dicts_path_indexing():
    shape = [{"title": str, "n": int}]
    obj = [{"title": "A", "n": 1}, {"title": "B"}]
    out = validate_shape(obj, shape)
    assert len(out) == 1
    assert out[0].path == "[1].n"


def test_dict_with_list_field():
    shape = {"actions": [{"title": str, "owner": str}]}
    obj = {"actions": [{"title": "T", "owner": "PM"}]}
    assert validate_shape(obj, shape) == []


def test_dict_with_list_field_path():
    shape = {"actions": [{"title": str, "owner": str}]}
    obj = {"actions": [{"title": "T", "owner": 42}]}
    out = validate_shape(obj, shape)
    assert len(out) == 1
    assert out[0].path == "actions[0].owner"


# ── Real-world-ish: byrxj StructuredReport ─────────────────────────────


BYRXJ_REPORT_SHAPE = {
    "headline": str,
    "blindSpot": {"dimension": str, "title": str, "body": str},
    "leverage": {"dimension": str, "title": str, "body": str},
    "actions": [
        {
            "number": int,
            "title": str,
            "owner": str,
            "timeframe": str,
            "body": str,
        }
    ],
}


def test_byrxj_report_valid():
    obj = {
        "headline": "Pathology named",
        "blindSpot": {"dimension": "Identity", "title": "T", "body": "B"},
        "leverage": {"dimension": "Trust", "title": "T", "body": "B"},
        "actions": [
            {"number": 1, "title": "T", "owner": "PM", "timeframe": "W1-2", "body": "B"},
            {"number": 2, "title": "T", "owner": "PM", "timeframe": "W3-6", "body": "B"},
            {"number": 3, "title": "T", "owner": "PM", "timeframe": "W7-12", "body": "B"},
        ],
    }
    assert validate_shape(obj, BYRXJ_REPORT_SHAPE) == []


def test_byrxj_report_missing_action_field():
    obj = {
        "headline": "x",
        "blindSpot": {"dimension": "x", "title": "x", "body": "x"},
        "leverage": {"dimension": "x", "title": "x", "body": "x"},
        "actions": [{"number": 1, "title": "T", "owner": "PM", "timeframe": "W1-2"}],  # body missing
    }
    out = validate_shape(obj, BYRXJ_REPORT_SHAPE)
    assert len(out) == 1
    assert out[0].path == "actions[0].body"


# ── assert_shape (raise-on-failure) ───────────────────────────────────────


def test_assert_shape_returns_obj_on_success():
    obj = {"name": "K"}
    out = assert_shape(obj, {"name": str}, source="test")
    assert out is obj


def test_assert_shape_raises_on_failure():
    with pytest.raises(ValueError) as exc:
        assert_shape({"name": 42}, {"name": str}, source="weekly_email")
    msg = str(exc.value)
    assert "weekly_email" in msg
    assert "name" in msg
    assert "expected str" in msg


def test_assert_shape_lists_all_violations():
    obj = {"a": 1, "b": "x", "c": 1.0}
    with pytest.raises(ValueError) as exc:
        assert_shape(obj, {"a": str, "b": int, "c": str}, source="t")
    msg = str(exc.value)
    assert msg.count("- ") == 3  # all three violations listed


# ── Edge cases ────────────────────────────────────────────────────────────


def test_int_or_float_for_number_field():
    """Common Claude pattern: number field that may come back as int or float."""
    shape = {"score": (int, float)}
    assert validate_shape({"score": 42}, shape) == []
    assert validate_shape({"score": 3.14}, shape) == []
    assert validate_shape({"score": "high"}, shape)  # not empty


def test_empty_list_passes_list_shape():
    assert validate_shape([], [str]) == []
