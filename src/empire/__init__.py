"""empire-lib — shared infrastructure for the empire.

Hard-block, not warn. Misuse raises exceptions; nothing is silently logged
as a warning unless the spec explicitly requires fail-silent (only the
usage logger does, because accounting must never break the main API call).
"""
from __future__ import annotations

__version__ = "0.1.0"

from empire.exceptions import (
    EmailLogPersistFailed,
    EmpireLibError,
    LiveAPIBlocked,
    MissingEnvVars,
    MissingTelemetryContext,
    MissingTrackingContext,
    OpusModelBlocked,
    ResendKeyMissing,
    SupabaseCredsNotFound,
)

__all__ = [
    "__version__",
    "EmpireLibError",
    "SupabaseCredsNotFound",
    "MissingEnvVars",
    "OpusModelBlocked",
    "MissingTelemetryContext",
    "ResendKeyMissing",
    "MissingTrackingContext",
    "EmailLogPersistFailed",
    "LiveAPIBlocked",
]
