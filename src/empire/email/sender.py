"""Resend sender. Tracking pairing is INTENDED but does not currently work.

Empire rule (feedback_resend_must_pair_email_log.md): every send to a
known subscriber must produce an email_log row keyed by resend_id, otherwise
the engagement webhook silently drops opens/clicks. This module makes the
pairing the *only* code path; calling without user_id/profile_person_key
raises MissingTrackingContext.

READ THIS BEFORE RELYING ON THE ABOVE (verified 2026-08-02 against the live
DB via information_schema + pg_indexes on project ejvavmpieilvigjktugh):
the email_log row is NEVER actually written. `_insert_email_log` targets a
table that belongs to AstroMedha's daily-guidance mailer, not a generic email
log, and it rejects our row for four independent reasons:

  1. `recipient` column does not exist                       -> 42703
  2. `subject` column does not exist                         -> 42703
  3. `guidance_date` is NOT NULL, no default, never supplied -> 23502
  4. `user_id` is a uuid; we type it `str`                   -> 22P02

Any one of these kills the insert, so the send always ends in
EmailLogPersistFailed *after* the mail has already gone out. The
"supabase creds not found" message you are most likely to see is only the
FIRST gate and masks all four. Do not read it as cosmetic.

So: **if you need open/click tracking, you have to build it first.** Do not
assume this module gives it to you. Do NOT fix it by adding recipient/subject
columns either — you would also have to drop the `guidance_date` NOT NULL
constraint, and that constraint is load-bearing for the mailer that actually
owns the table. The real fix is a separate table owned by empire-lib, with
the engagement webhook reading both. That is deliberately not built: as of
2026-08-02 this sender had no live production caller (checked by grepping
`from empire.email import` across the local tree only), so the table would
have served nobody. Build it when a real consumer needs tracking, not before.

Empire rule (feedback_no_fabricated_ui_surfaces.md): every send may pass an
optional `frontend_root` to lint the HTML body for fabricated UI references
("update it on the Profile page" when no Profile page exists). When the path
is provided (or EMPIRE_FRONTEND_ROOT env var is set), the send hard-aborts
with UnverifiedUIClaim before hitting Resend.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

from empire.config.supabase_creds import get_supabase_creds
from empire.exceptions import (
    CopyGuardViolation,
    EmailLogPersistFailed,
    MissingTrackingContext,
    ResendKeyMissing,
    SupabaseCredsNotFound,
    UnverifiedUIClaim,
)
from empire.email.mobile import assert_phone_safe
from empire.lint.copy_guards import check_all, has_blocking
from empire.lint.ui_claims import lint_outbound_copy

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
    send happened but tracking is now lost.

    NOTE: this insert cannot currently succeed — the row shape below does not
    match the live table (four independent blockers, see module docstring), so
    "accept the gap" is the only available option and backfilling by hand is
    not one. Kept as-is rather than quietly deleted: the intent is right and
    the loud failure is more honest than silently sending untracked mail.
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
    frontend_root: str | Path | None = None,
    copy_guard_context: str | None = None,
    allow_overflow_risks: bool = False,
) -> dict:
    """Send a Resend email. Attempts the paired email_log row, which currently
    always fails — see the module docstring. The email itself does send.

    All args after `*` are required kwargs. Calling with positional args or
    missing user_id / profile_person_key raises MissingTrackingContext.

    `frontend_root` (or env var EMPIRE_FRONTEND_ROOT) enables the UI-claim
    linter — the html body is checked against the live frontend's real UI
    surfaces and the send aborts with UnverifiedUIClaim if any reference
    can't be resolved. Pass the project's Next.js / Streamlit / static-HTML
    root. Omit (and leave the env var unset) to skip the check.

    `copy_guard_context` (e.g. "kbk_reel", "kbk_curtain", "general") opts
    the send into the copy_guards catalog. Block-severity violations
    (Sanganer in a KBK reel, "natural-dye" on a curtain product page, etc)
    raise CopyGuardViolation. Warn-level violations (AI-writing tells)
    print to stderr but do not block. Omit to skip these checks.

    Every send is checked for phone-safety first and raises EmailNotPhoneSafe
    if the HTML would clip on a 360px screen — fixed widths, a missing
    viewport, a wide data table, a long nowrap run, a duplicate style attr.
    This sender bypasses the resend-send edge function by design, so nothing
    downstream will fix the HTML for it. Pass allow_overflow_risks=True only
    when the overflow is deliberate and the recipient reads on desktop.

    Returns the Resend JSON response (contains the `id` field).
    Raises:
    - MissingTrackingContext if user_id or profile_person_key is empty.
    - UnverifiedUIClaim if the html body references UI surfaces that don't
      exist in the resolved frontend.
    - CopyGuardViolation if a block-severity copy guard fires.
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

    # UI-claim lint: hard-stop on any unverified surface reference. Resolved
    # in this order: explicit kwarg > env var > skip. The env-var path lets a
    # whole project opt in once at startup without threading kwargs through
    # every call site.
    resolved_root = frontend_root or os.environ.get("EMPIRE_FRONTEND_ROOT")
    if resolved_root:
        lint = lint_outbound_copy(html, Path(resolved_root))
        if not lint.ok:
            raise UnverifiedUIClaim(lint.unverified)

    # Copy guards: optional domain-specific rule check. Block-severity
    # violations (Sanganer, natural-dye claim, etc) hard-stop the send;
    # warn-level (AI-writing tells) print to stderr but don't block.
    if copy_guard_context:
        violations = check_all(html, context=copy_guard_context)
        if has_blocking(violations):
            raise CopyGuardViolation(violations)
        for v in violations:
            print(
                f"[empire.email] copy guard warn: {v.guard}: {v.rule} "
                f"-- snippet=...{v.snippet.strip()}...",
                file=sys.stderr,
            )

    # Phone-safety guard. This sender posts straight to api.resend.com on
    # purpose (it is the one path that survives a resend-send edge-function
    # outage), which means it never gets that function's mobile-safe rewrite.
    # So detect here instead: an email that would clip on a phone fails on the
    # author, not on the reader. Detection only — the author's HTML is never
    # rewritten. (2026-08-02: a hand-built email went out through this path
    # with no phone check of any kind behind it.)
    if not allow_overflow_risks:
        assert_phone_safe(html)

    api_key = _resolve_resend_key()

    # Guard: any From: address whose mailbox doesn't actually receive mail
    # creates a reply-bounce trap (recipient hits Reply, mailer-daemon
    # rejects). Auto-default reply_to to a real inbox so this never
    # happens. Patterns matched: noreply@, no-reply@, watch@, alerts@,
    # notifications@, empire@, digest@, support@ on rxjapps.in domains.
    if not reply_to:
        lowered = (from_email or "").lower()
        TRAP_PREFIXES = ("noreply@", "no-reply@", "watch@", "alerts@",
                         "notifications@", "empire@", "digest@", "support@")
        if any(p in lowered for p in TRAP_PREFIXES):
            reply_to = "jindal.rahul@gmail.com"

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
