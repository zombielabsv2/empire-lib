"""Resend sender with mandatory email_log pairing.

Empire rule (feedback_resend_must_pair_email_log.md): every send to a
known subscriber must produce an email_log row keyed by resend_id, otherwise
the engagement webhook silently drops opens/clicks. This module makes the
pairing the *only* code path; calling without user_id/profile_person_key
raises MissingTrackingContext.
"""
from __future__ import annotations

import os
import sys

import httpx

from empire.config.supabase_creds import get_supabase_creds
from empire.exceptions import (
    EmailLogPersistFailed,
    MissingTrackingContext,
    ResendKeyMissing,
    SupabaseCredsNotFound,
)

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "noreply@rxjapps.in"


def _resolve_resend_key() -> str:
    """Return RESEND_API_KEY from env; raise ResendKeyMissing if unset."""
    if "RESEND_API_KEY" not in os.environ or not os.environ["RESEND_API_KEY"]:
        raise ResendKeyMissing(
            "RESEND_API_KEY is not set in the environment. "
            "Set it before calling send_email_tracked."
        )
    return os.environ["RESEND_API_KEY"]


def _insert_email_log(
    *,
    resend_id: str,
    recipient: str,
    user_id: str,
    profile_person_key: str,
    subject: str,
) -> None:
    """Insert a row into Supabase email_log keyed by resend_id.

    Raises EmailLogPersistFailed on any failure so the operator knows the
    send happened but tracking is now lost. Caller decides whether to
    backfill or accept the gap.
    """
    try:
        url, key = get_supabase_creds()
    except SupabaseCredsNotFound as e:
        raise EmailLogPersistFailed(resend_id, f"supabase creds not found: {e}") from e

    row = {
        "resend_id": resend_id,
        "recipient": recipient,
        "user_id": user_id,
        "profile_person_key": profile_person_key,
        "subject": (subject or "")[:500],
    }
    try:
        resp = httpx.post(
            f"{url}/rest/v1/email_log",
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
        raise EmailLogPersistFailed(
            resend_id, f"{type(e).__name__}: {e}"
        ) from e

    if resp.status_code >= 300:
        raise EmailLogPersistFailed(
            resend_id,
            f"http {resp.status_code}: {resp.text[:200]}",
        )


def send_email_tracked(
    *,
    to: str,
    subject: str,
    html: str,
    user_id: str,
    profile_person_key: str,
    from_email: str = DEFAULT_FROM,
    reply_to: str | None = None,
) -> dict:
    """Send a Resend email and write the paired email_log row.

    All args after `*` are required kwargs. Calling with positional args or
    missing user_id / profile_person_key raises MissingTrackingContext.

    Returns the Resend JSON response (contains the `id` field).
    Raises:
    - MissingTrackingContext if user_id or profile_person_key is empty.
    - ResendKeyMissing if RESEND_API_KEY is unset.
    - httpx.HTTPStatusError on Resend non-2xx.
    - EmailLogPersistFailed if the email_log insert fails (send already happened).
    """
    if not user_id or not profile_person_key:
        raise MissingTrackingContext(
            "send_email_tracked requires non-empty user_id and "
            "profile_person_key (feedback_resend_must_pair_email_log.md)."
        )
    if not to:
        raise MissingTrackingContext("send_email_tracked requires non-empty `to`.")

    api_key = _resolve_resend_key()

    body: dict = {
        "from": from_email,
        "to": to,
        "subject": subject,
        "html": html,
    }
    if reply_to:
        body["reply_to"] = reply_to

    resp = httpx.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30.0,
    )
    resp.raise_for_status()

    data = resp.json() or {}
    resend_id = data.get("id", "")
    if not resend_id:
        # Resend returned 2xx but no id — treat as tracking failure.
        raise EmailLogPersistFailed(
            "<no-id>", f"Resend response missing id field: {data!r}"
        )

    try:
        _insert_email_log(
            resend_id=resend_id,
            recipient=to,
            user_id=user_id,
            profile_person_key=profile_person_key,
            subject=subject,
        )
    except EmailLogPersistFailed as e:
        # Loud warning + re-raise so operator knows tracking was lost.
        print(
            f"[empire.email] WARNING: send to {to} succeeded "
            f"(resend_id={resend_id}) but email_log insert failed: {e.reason}",
            file=sys.stderr,
        )
        raise

    return data
