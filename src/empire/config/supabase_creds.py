"""Supabase credential resolver — multi-layout secret resolution.

Resolution order:
1. Environment variables (Cloud Run, GitHub Actions)
2. Top-level st.secrets keys (Streamlit Cloud, simple layout)
3. Nested TOML sections: [supabase], [connections.supabase], [database]

Caches ONLY positive results. Negative case is re-checked every call so a
late-arriving secrets layer or transient cold-start lookup failure doesn't
poison the whole process. (KBK Approvals 2026-04-25 incident: cached
_available=False kept the page broken across reboots even when secrets
were sitting in the Streamlit Cloud UI.)
"""
from __future__ import annotations

import os

from empire.exceptions import SupabaseCredsNotFound

_URL_KEYS: tuple[str, ...] = ("SUPABASE_URL",)
_SERVICE_KEYS: tuple[str, ...] = (
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_KEY",
)

# Positive-only cache. Never store the (None, None) miss case.
_cache: dict[str, str] = {}


def reset_cache() -> None:
    """Clear the positive resolver cache. Mainly for tests."""
    _cache.clear()


def _resolve_from_env() -> tuple[str, str]:
    """Read SUPABASE_URL + first available service key from env. Empty strings on miss."""
    url = ""
    if "SUPABASE_URL" in os.environ:
        url = os.environ["SUPABASE_URL"]
    key = ""
    for k in _SERVICE_KEYS:
        if k in os.environ and os.environ[k]:
            key = os.environ[k]
            break
    return url, key


def _resolve_from_streamlit_secrets() -> tuple[str, str]:
    """Try top-level + nested st.secrets layouts. Empty strings on miss / no streamlit."""
    try:
        import streamlit as st
    except Exception:
        return "", ""

    # Top-level keys
    url = ""
    for k in _URL_KEYS:
        try:
            v = st.secrets.get(k, "")
        except Exception:
            v = ""
        if v:
            url = str(v)
            break
    key = ""
    for k in _SERVICE_KEYS:
        try:
            v = st.secrets.get(k, "")
        except Exception:
            v = ""
        if v:
            key = str(v)
            break
    if url and key:
        return url, key

    # Nested TOML sections
    for section_name in ("supabase", "connections.supabase", "database"):
        try:
            section = st.secrets.get(section_name, {})
        except Exception:
            continue
        if not section:
            continue
        try:
            sec_url = (
                section.get("SUPABASE_URL", "")
                or section.get("url", "")
            )
            sec_key = (
                section.get("SUPABASE_SERVICE_KEY", "")
                or section.get("SUPABASE_SERVICE_ROLE_KEY", "")
                or section.get("service_key", "")
                or section.get("service_role_key", "")
                or section.get("SUPABASE_KEY", "")
                or section.get("key", "")
            )
            if sec_url and sec_key:
                return str(sec_url), str(sec_key)
        except Exception:
            continue
    return url, key


def get_supabase_creds() -> tuple[str, str]:
    """Resolve (url, service_key); raise SupabaseCredsNotFound on miss.

    Caches positive results only. The negative case is re-checked every call.
    """
    if "url" in _cache and "key" in _cache:
        return _cache["url"], _cache["key"]

    url, key = _resolve_from_env()
    if not url or not key:
        s_url, s_key = _resolve_from_streamlit_secrets()
        url = url or s_url
        key = key or s_key

    if not url or not key:
        raise SupabaseCredsNotFound(
            "Supabase URL+key not found in env (SUPABASE_URL + one of "
            f"{list(_SERVICE_KEYS)}) or st.secrets (top-level / [supabase] / "
            "[connections.supabase] / [database])."
        )

    _cache["url"] = url.rstrip("/")
    _cache["key"] = key
    return _cache["url"], _cache["key"]


def diagnose_creds() -> dict:
    """Return non-sensitive diagnostic info about WHERE creds were found.

    Never returns the secret value itself — only key names + presence info.
    Useful for surfacing 'secrets missing' in deployed UIs without leaking.
    """
    diag: dict = {
        "resolved": False,
        "env_keys_seen": [],
        "secret_top_keys": [],
        "secret_sections": [],
    }
    if "SUPABASE_URL" in os.environ:
        diag["env_keys_seen"].append("SUPABASE_URL")
    for k in _SERVICE_KEYS:
        if k in os.environ:
            diag["env_keys_seen"].append(k)
    try:
        import streamlit as st
        try:
            top = list(st.secrets.keys())
            diag["secret_top_keys"] = sorted(
                k for k in top
                if "SUPABASE" in k.upper()
                or k.lower() in ("supabase", "database", "connections")
            )
        except Exception as e:
            diag["secret_top_keys_error"] = type(e).__name__
        for s in ("supabase", "connections.supabase", "database"):
            try:
                sec = st.secrets.get(s, {})
                if sec:
                    diag["secret_sections"].append(
                        f"[{s}] keys: {sorted(sec.keys())}"
                    )
            except Exception:
                pass
    except Exception:
        pass
    try:
        url, key = get_supabase_creds()
        diag["resolved"] = True
        diag["url"] = url
        diag["key_len"] = len(key)
        diag["key_prefix"] = key[:11] + "..."
    except SupabaseCredsNotFound:
        pass
    return diag
