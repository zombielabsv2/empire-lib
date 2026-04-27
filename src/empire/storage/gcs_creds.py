"""GCS credential resolver — env JSON > st.secrets JSON > ADC.

Resolution order:
1. GCP_SA_KEY env var (raw JSON string, set by Vercel + Cloud Run mounts)
2. st.secrets.GCP_SA_KEY (Streamlit Cloud)
3. Application Default Credentials (Cloud Run metadata server,
   gcloud auth application-default login on dev)

Caches positive results only — a missing-creds state is re-checked every
call so a late-arriving secret layer doesn't poison the process.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from empire.exceptions import GCSCredsNotFound

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

GCS_KEY_ENV = "GCP_SA_KEY"

_cache: dict[str, object] = {}


def reset_cache() -> None:
    """Clear cached credentials. Mainly for tests."""
    _cache.clear()


def _from_json_string(raw: str) -> "Credentials":
    """Build service-account credentials from a raw JSON string."""
    from google.oauth2 import service_account

    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info)


def _resolve_from_env() -> "Credentials | None":
    if GCS_KEY_ENV in os.environ and os.environ[GCS_KEY_ENV]:
        return _from_json_string(os.environ[GCS_KEY_ENV])
    return None


def _resolve_from_streamlit_secrets() -> "Credentials | None":
    try:
        import streamlit as st
    except Exception:
        return None
    try:
        v = st.secrets.get(GCS_KEY_ENV, "")
    except Exception:
        v = ""
    if v:
        return _from_json_string(str(v))
    return None


def _resolve_from_adc() -> "Credentials | None":
    try:
        from google.auth import default as default_credentials

        creds, _project = default_credentials()
        return creds
    except Exception:
        return None


def get_credentials() -> "Credentials":
    """Return a Google Auth Credentials instance for GCS reads/writes.

    Raises GCSCredsNotFound if no source resolves.
    """
    if "creds" in _cache:
        return _cache["creds"]  # type: ignore[return-value]

    creds = _resolve_from_env() or _resolve_from_streamlit_secrets() or _resolve_from_adc()
    if creds is None:
        raise GCSCredsNotFound(
            "GCS creds not found. Set GCP_SA_KEY env var (raw JSON), put it "
            "in st.secrets, or run on a host with Application Default "
            "Credentials (Cloud Run / gcloud auth application-default login)."
        )
    _cache["creds"] = creds
    return creds
