"""Parity dry-run: KBK utils.supabase_data.get_supabase_creds vs empire-lib's.

Read-only — does not modify KBK or empire-lib code. Run locally:
    python scripts/parity_kbk_supabase_resolver.py

Tests both resolvers against identical fabricated env + st.secrets layouts and
prints a parity matrix. Drives the "should we migrate?" decision.

Known expected divergence (handled below, not a failure):
- KBK returns None on miss; empire-lib raises SupabaseCredsNotFound. The
  parity wrapper converts the exception to None so we can compare semantics.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
KBK_PATH = ROOT.parent / "kari-growth-platform"
EMPIRE_SRC = ROOT / "src"

# Make both packages importable
sys.path.insert(0, str(EMPIRE_SRC))
sys.path.insert(0, str(KBK_PATH))


# ---------------------------------------------------------------------------
# Fake streamlit for st.secrets mocking
# ---------------------------------------------------------------------------
class _FakeSecrets:
    def __init__(self, data: dict):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def __contains__(self, key):
        return key in self._data


def _install_streamlit(secrets_data: dict | None):
    fake = types.ModuleType("streamlit")
    fake.secrets = _FakeSecrets(secrets_data or {})
    sys.modules["streamlit"] = fake


def _uninstall_streamlit():
    sys.modules.pop("streamlit", None)


# ---------------------------------------------------------------------------
# Reset module caches between scenarios
# ---------------------------------------------------------------------------
def _reset_caches():
    # KBK module-level caches
    sys.modules.pop("utils.supabase_data", None)
    sys.modules.pop("utils", None)
    # Empire-lib cache
    from empire.config.supabase_creds import reset_cache
    reset_cache()


# ---------------------------------------------------------------------------
# Resolver wrappers (normalize: both return tuple | None)
# ---------------------------------------------------------------------------
def _run_kbk():
    from utils.supabase_data import get_supabase_creds
    return get_supabase_creds()


def _run_empire():
    from empire.config.supabase_creds import get_supabase_creds
    from empire.exceptions import SupabaseCredsNotFound
    try:
        return get_supabase_creds()
    except SupabaseCredsNotFound:
        return None


# ---------------------------------------------------------------------------
# Scenarios — realistic Supabase secret layouts seen across the empire
# ---------------------------------------------------------------------------
SCENARIOS = [
    {
        "name": "env-only: SUPABASE_URL + SUPABASE_SERVICE_KEY",
        "env": {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SERVICE_KEY": "key-svc"},
        "secrets": None,
        "expected": ("https://abc.supabase.co", "key-svc"),
    },
    {
        "name": "env-only: SUPABASE_URL + legacy SUPABASE_KEY",
        "env": {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_KEY": "key-legacy"},
        "secrets": None,
        "expected": ("https://abc.supabase.co", "key-legacy"),
    },
    {
        "name": "env-only: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY",
        "env": {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "key-role"},
        "secrets": None,
        "expected": ("https://abc.supabase.co", "key-role"),
    },
    {
        "name": "env URL with trailing slash -> must be stripped",
        "env": {"SUPABASE_URL": "https://abc.supabase.co/", "SUPABASE_SERVICE_KEY": "k"},
        "secrets": None,
        "expected": ("https://abc.supabase.co", "k"),
    },
    {
        "name": "st.secrets top-level only (no env)",
        "env": {},
        "secrets": {"SUPABASE_URL": "https://t.supabase.co", "SUPABASE_SERVICE_KEY": "top-key"},
        "expected": ("https://t.supabase.co", "top-key"),
    },
    {
        "name": "st.secrets [supabase] nested",
        "env": {},
        "secrets": {"supabase": {"SUPABASE_URL": "https://n.supabase.co", "SUPABASE_SERVICE_KEY": "nested"}},
        "expected": ("https://n.supabase.co", "nested"),
    },
    {
        "name": "st.secrets [supabase] with lowercase url/service_key",
        "env": {},
        "secrets": {"supabase": {"url": "https://lc.supabase.co", "service_key": "lc-key"}},
        "expected": ("https://lc.supabase.co", "lc-key"),
    },
    {
        "name": "st.secrets [connections.supabase] nested",
        "env": {},
        "secrets": {"connections.supabase": {"SUPABASE_URL": "https://c.supabase.co", "SUPABASE_SERVICE_KEY": "conn-key"}},
        "expected": ("https://c.supabase.co", "conn-key"),
    },
    {
        "name": "st.secrets [database] nested",
        "env": {},
        "secrets": {"database": {"url": "https://d.supabase.co", "service_role_key": "db-key"}},
        "expected": ("https://d.supabase.co", "db-key"),
    },
    {
        "name": "miss: nothing set",
        "env": {},
        "secrets": None,
        "expected": None,
    },
    {
        "name": "partial: env URL, no key, st.secrets top-level provides key",
        "env": {"SUPABASE_URL": "https://hybrid.supabase.co"},
        "secrets": {"SUPABASE_SERVICE_KEY": "from-secrets"},
        "expected": ("https://hybrid.supabase.co", "from-secrets"),
    },
    {
        "name": "env wins over st.secrets when both set",
        "env": {"SUPABASE_URL": "https://env-wins.supabase.co", "SUPABASE_SERVICE_KEY": "env-key"},
        "secrets": {"SUPABASE_URL": "https://lose.supabase.co", "SUPABASE_SERVICE_KEY": "loser"},
        "expected": ("https://env-wins.supabase.co", "env-key"),
    },
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
SUPABASE_ENV_VARS = (
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
)


def _clear_supabase_env():
    for k in SUPABASE_ENV_VARS:
        os.environ.pop(k, None)


def run_scenario(s: dict) -> dict:
    _clear_supabase_env()
    for k, v in (s["env"] or {}).items():
        os.environ[k] = v
    if s["secrets"] is not None:
        _install_streamlit(s["secrets"])
    else:
        _uninstall_streamlit()
    _reset_caches()

    try:
        kbk_result = _run_kbk()
    except Exception as e:
        kbk_result = f"RAISED: {type(e).__name__}: {e}"

    _reset_caches()

    try:
        emp_result = _run_empire()
    except Exception as e:
        emp_result = f"RAISED: {type(e).__name__}: {e}"

    return {
        "name": s["name"],
        "expected": s["expected"],
        "kbk": kbk_result,
        "empire": emp_result,
        "kbk_matches_expected": kbk_result == s["expected"],
        "empire_matches_expected": emp_result == s["expected"],
        "parity": kbk_result == emp_result,
    }


def main():
    print("=" * 78)
    print("PARITY DRY-RUN: KBK supabase resolver  vs  empire-lib supabase resolver")
    print("=" * 78)
    print()

    results = [run_scenario(s) for s in SCENARIOS]

    parity_count = sum(1 for r in results if r["parity"])
    kbk_correct = sum(1 for r in results if r["kbk_matches_expected"])
    emp_correct = sum(1 for r in results if r["empire_matches_expected"])
    total = len(results)

    print(f"Parity: {parity_count}/{total}   "
          f"KBK correct: {kbk_correct}/{total}   "
          f"Empire correct: {emp_correct}/{total}")
    print()

    for r in results:
        status = "PASS" if r["parity"] else "DIFF"
        print(f"[{status}] {r['name']}")
        if not r["parity"] or not r["empire_matches_expected"]:
            print(f"   expected: {r['expected']!r}")
            print(f"   kbk:      {r['kbk']!r}")
            print(f"   empire:   {r['empire']!r}")
    print()

    if parity_count == total and emp_correct == total and kbk_correct == total:
        print("RESULT: full parity. Migration is safe at the resolver layer.")
        return 0
    else:
        print("RESULT: divergence detected. Review above before migrating.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
