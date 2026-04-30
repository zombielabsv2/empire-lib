"""Pre-send lint utilities for empire outbound channels.

The single hallucination-class that birthed this package was an autonomous
agent claiming a UI surface exists when it doesn't ("update it on the Profile
page" -> no such page). Memory rules don't bind autopilots; code guards do.

See `empire.lint.ui_claims` for the concrete linter.
"""

from empire.lint.copy_guards import (
    CONTEXT_GENERAL,
    CONTEXT_KBK_CURTAIN,
    CONTEXT_KBK_REEL,
    Violation,
    check_ai_writing,
    check_all,
    check_kbk_curtain,
    check_kbk_reel,
    check_no_opus_on_api,
    format_report,
    has_blocking,
)
from empire.lint.json_shape import (
    ShapeViolation,
    assert_shape,
    validate_shape,
)
from empire.lint.ui_claims import (
    LintResult,
    discover_ui_surfaces,
    extract_ui_claims,
    lint_outbound_copy,
)

__all__ = [
    "CONTEXT_GENERAL",
    "CONTEXT_KBK_CURTAIN",
    "CONTEXT_KBK_REEL",
    "LintResult",
    "ShapeViolation",
    "Violation",
    "assert_shape",
    "check_ai_writing",
    "check_all",
    "check_kbk_curtain",
    "check_kbk_reel",
    "check_no_opus_on_api",
    "discover_ui_surfaces",
    "extract_ui_claims",
    "format_report",
    "has_blocking",
    "lint_outbound_copy",
    "validate_shape",
]
