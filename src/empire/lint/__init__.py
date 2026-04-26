"""Pre-send lint utilities for empire outbound channels.

The single hallucination-class that birthed this package was an autonomous
agent claiming a UI surface exists when it doesn't ("update it on the Profile
page" -> no such page). Memory rules don't bind autopilots; code guards do.

See `empire.lint.ui_claims` for the concrete linter.
"""

from empire.lint.ui_claims import (
    LintResult,
    discover_ui_surfaces,
    extract_ui_claims,
    lint_outbound_copy,
)

__all__ = [
    "LintResult",
    "discover_ui_surfaces",
    "extract_ui_claims",
    "lint_outbound_copy",
]
