"""Tests for empire.config.env_check."""
from __future__ import annotations

import os

import pytest

from empire.config.env_check import is_set, require_env
from empire.exceptions import MissingEnvVars


def test_require_env_returns_values_when_all_present(monkeypatch):
    monkeypatch.setenv("EMPIRE_TEST_A", "alpha")
    monkeypatch.setenv("EMPIRE_TEST_B", "beta")
    out = require_env("EMPIRE_TEST_A", "EMPIRE_TEST_B")
    assert out == {"EMPIRE_TEST_A": "alpha", "EMPIRE_TEST_B": "beta"}


def test_require_env_raises_with_missing_keys_listed(monkeypatch):
    monkeypatch.delenv("EMPIRE_TEST_MISSING_X", raising=False)
    monkeypatch.delenv("EMPIRE_TEST_MISSING_Y", raising=False)
    monkeypatch.setenv("EMPIRE_TEST_PRESENT", "1")
    with pytest.raises(MissingEnvVars) as exc_info:
        require_env("EMPIRE_TEST_PRESENT", "EMPIRE_TEST_MISSING_X", "EMPIRE_TEST_MISSING_Y")
    assert exc_info.value.keys == ["EMPIRE_TEST_MISSING_X", "EMPIRE_TEST_MISSING_Y"]


def test_require_env_treats_empty_string_as_set(monkeypatch):
    """Empty string is a valid dry-run signal — must NOT trigger MissingEnvVars."""
    monkeypatch.setenv("EMPIRE_TEST_DRY_RUN", "")
    out = require_env("EMPIRE_TEST_DRY_RUN")
    assert out == {"EMPIRE_TEST_DRY_RUN": ""}


def test_is_set_true_for_empty_string(monkeypatch):
    monkeypatch.setenv("EMPIRE_TEST_FLAG", "")
    assert is_set("EMPIRE_TEST_FLAG") is True


def test_is_set_false_when_absent(monkeypatch):
    monkeypatch.delenv("EMPIRE_TEST_NOT_THERE", raising=False)
    assert is_set("EMPIRE_TEST_NOT_THERE") is False
