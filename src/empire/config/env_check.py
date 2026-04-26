"""Env-var checking utilities with `k not in os.environ` semantics.

The empire rule (feedback_empty_string_env_check.md): empty-string values
are intentional dry-run signals and must NOT be treated as missing. Use
`k not in os.environ`, never `not os.environ.get(k)`.
"""
from __future__ import annotations

import os

from empire.exceptions import MissingEnvVars


def require_env(*keys: str) -> dict[str, str]:
    """Return env values for all `keys`; raise MissingEnvVars listing missing ones.

    A key is "missing" only if it is not present in os.environ. Empty string
    counts as set (intentional dry-run signal — don't block).
    """
    missing = [k for k in keys if k not in os.environ]
    if missing:
        raise MissingEnvVars(missing)
    return {k: os.environ[k] for k in keys}


def is_set(key: str) -> bool:
    """Return True if `key` is present in os.environ, regardless of value.

    Empty string returns True. Use this to detect intentional dry-run flags.
    """
    return key in os.environ
