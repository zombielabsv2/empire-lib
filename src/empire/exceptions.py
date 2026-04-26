"""All custom exceptions for empire-lib in one place.

Hard-block philosophy: these are raised on misuse, never logged as warnings.
Advisory rules don't prevent autopilots from violating them — code guards do.
"""
from __future__ import annotations


class EmpireLibError(Exception):
    """Base class for all empire-lib exceptions."""


# --- Config / credential resolution ---


class SupabaseCredsNotFound(EmpireLibError):
    """Raised when Supabase URL+key cannot be resolved from env or st.secrets."""


class MissingEnvVars(EmpireLibError):
    """Raised when required environment variables are not set.

    Note: empty-string values count as set (intentional dry-run signal).
    Only true absence (k not in os.environ) triggers this.
    """

    def __init__(self, keys: list[str]):
        self.keys = list(keys)
        super().__init__(
            f"missing required env vars: {', '.join(self.keys)}"
        )


# --- Anthropic API ---


class OpusModelBlocked(EmpireLibError):
    """Raised when an Opus model is requested. Empire-wide ban.

    Reason: feedback_no_opus_on_api.md — Opus is banned on the Anthropic API
    empire-wide. Default to Sonnet. This guard exists so an autopilot that
    didn't read the memo can't accidentally rack up 5x cost.
    """


class MissingTelemetryContext(EmpireLibError):
    """Raised when post_messages is called without app + action kwargs.

    Reason: every Claude call must be attributable in api_usage_log so cost
    reports actually identify which feature/cron drove spend. Untagged rows
    show up as 'unknown' and cause hours of forensics later.
    """


# --- Email / Resend ---


class ResendKeyMissing(EmpireLibError):
    """Raised when RESEND_API_KEY is not set in the environment."""


class MissingTrackingContext(EmpireLibError):
    """Raised when send_email_tracked is missing user_id/profile_person_key.

    Reason: feedback_resend_must_pair_email_log.md — every send to a known
    subscriber must produce an email_log row keyed by resend_id, otherwise
    the engagement webhook silently drops opens/clicks.
    """


class EmailLogPersistFailed(EmpireLibError):
    """Raised when the email_log row insert fails after a successful Resend send.

    The send happened (resend_id exists) but tracking is now lost. Operator
    must know so they can either backfill the row or accept the gap.
    """

    def __init__(self, resend_id: str, reason: str):
        self.resend_id = resend_id
        self.reason = reason
        super().__init__(
            f"email sent (resend_id={resend_id}) but email_log insert failed: {reason}"
        )


# --- Test guards ---


class LiveAPIBlocked(EmpireLibError):
    """Raised by empire.test.guards when a test attempts a live API call.

    Tests must mock at the API boundary, not at the caller. This guard
    catches direct httpx/requests posts to api.anthropic.com, api.resend.com,
    graph.facebook.com, googleads.googleapis.com.
    """

    def __init__(self, host: str, url: str):
        self.host = host
        self.url = url
        super().__init__(
            f"live API call blocked in tests: {host} (url={url}). "
            f"Mock at the API boundary or use respx/responses."
        )
