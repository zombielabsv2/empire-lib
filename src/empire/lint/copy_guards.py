"""Pre-send copy guards for empire outbound channels.

Memory rules captured in ~/.claude/projects/.../memory/ are *advice* — they
bind Claude in conversation but autopilots (Cloud Run jobs, GH Actions
crons, Resend wrappers) never read them. Every "Sanganer in KBK overlay"
or "fabricated brand fact" or em-dash in customer copy that slipped past
review came from this gap.

This module turns the load-bearing rules into runtime checks. A guard
returns a list of `Violation`s; `block` violations should hard-stop the
send, `warn` violations should log + continue.

The rule catalog mirrors specific feedback memories:
- feedback_kbk_no_sanganer_in_social_copy.md
- feedback_kbk_curtains_copy_facts.md
- feedback_kbk_reel_no_separators.md
- feedback_no_ai_writing.md
- feedback_no_fabricated_brand_facts.md (partial — see notes inline)
- feedback_no_opus_on_api.md (model-pin guard)

When a memory rule is added that maps cleanly to a regex/literal check,
add it here so the next agent run can't violate it silently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Violation:
    """One specific rule trigger inside a piece of copy."""

    guard: str  # short id of the guard that fired (for grep-ability)
    severity: str  # "block" or "warn"
    snippet: str  # ~40 chars of context around the match
    rule: str  # human-readable explanation of what was violated


# ── KBK reels (overlays / Stories) ────────────────────────────────────────

# Sanganer rule is defensive — never claim it because we can't prove it
# (Bagru is fine; the actual print region is mixed). "Block-printed" alone
# is enough. See feedback_kbk_no_sanganer_in_social_copy.md.
_KBK_REEL_BANNED_LITERALS: dict[str, str] = {
    "sanganer": (
        "KBK overlays/Stories: 'block-printed' alone is enough. Never claim "
        "Sanganer — the print region is mixed and we can't prove origin."
    ),
}

# feedback_kbk_reel_no_separators.md — middle-dot, em-dash, en-dash, and
# hyphen-as-glue are banned in reel overlays (use `|` for line breaks or
# split into beats). Compound-word hyphens are fine (block-printed,
# 5-7-day) so we only flag space-padded hyphens.
_KBK_REEL_SEPARATOR_PATTERNS: list[tuple[str, str]] = [
    (r"·", "Reel overlays: middle-dot (·) banned. Use `|` for line breaks or split into beats."),
    (r"—", "Reel overlays: em-dash (—) banned. Use `|` or split into beats."),
    (r"–", "Reel overlays: en-dash (–) banned. Use `|` or split into beats."),
    (r" - ", "Reel overlays: space-padded hyphen banned. Use `|` or split into beats."),
]


def check_kbk_reel(text: str) -> list[Violation]:
    """Pre-send guard for KBK reel overlay text + Story captions."""
    violations: list[Violation] = []
    lc = text.lower()
    for literal, rule in _KBK_REEL_BANNED_LITERALS.items():
        idx = lc.find(literal)
        if idx >= 0:
            violations.append(
                Violation(
                    guard="kbk_reel_no_sanganer",
                    severity="block",
                    snippet=text[max(0, idx - 20) : idx + len(literal) + 20],
                    rule=rule,
                )
            )
    for pat, rule in _KBK_REEL_SEPARATOR_PATTERNS:
        m = re.search(pat, text)
        if m:
            violations.append(
                Violation(
                    guard="kbk_reel_no_separators",
                    severity="block",
                    snippet=text[max(0, m.start() - 20) : m.end() + 20],
                    rule=rule,
                )
            )
    return violations


# ── KBK curtains (product / marketing copy) ───────────────────────────────

# feedback_kbk_curtains_copy_facts.md — explicitly NOT natural-dye, and
# the "no two panels are the same" line is fabrication.
_KBK_CURTAIN_BANNED_LITERALS: dict[str, str] = {
    "natural dye": "KBK curtains are NOT natural-dye. Lead with fabric (Kota/mulmul/flex).",
    "natural dyes": "KBK curtains are NOT natural-dye. Lead with fabric (Kota/mulmul/flex).",
    "natural-dye": "KBK curtains are NOT natural-dye. Lead with fabric (Kota/mulmul/flex).",
    "no two panels": "KBK curtains: 'no two panels are the same' is fabrication. Cut.",
    "no two pieces are the same": "KBK curtains: 'no two pieces are the same' is fabrication.",
}


def check_kbk_curtain(text: str) -> list[Violation]:
    """Pre-send guard for KBK curtain product / marketing copy."""
    violations: list[Violation] = []
    lc = text.lower()
    for literal, rule in _KBK_CURTAIN_BANNED_LITERALS.items():
        idx = lc.find(literal)
        if idx >= 0:
            violations.append(
                Violation(
                    guard="kbk_curtain_facts",
                    severity="block",
                    snippet=text[max(0, idx - 20) : idx + len(literal) + 20],
                    rule=rule,
                )
            )
    return violations


# ── AI writing tells ──────────────────────────────────────────────────────

# feedback_no_ai_writing.md — banned words/patterns from Wikipedia AI
# detection guide. Default severity is `warn` because these can sneak into
# legit prose; aisweep treats them as hard. Tune via `level=` if you want
# a stricter check on a specific surface.
_AI_TELL_PATTERNS: list[tuple[str, str]] = [
    # Em/en dashes — common AI tell in narrative copy.
    (r"—", "AI tell: em-dash. Replace with period or comma."),
    # Single-word AI tells (case-insensitive, word-boundary).
    (r"\bdelve\b", "AI tell: 'delve'."),
    (r"\btapestry\b", "AI tell: 'tapestry'."),
    (r"\btestament\b", "AI tell: 'testament'."),
    (r"\bvibrant\b", "AI tell: 'vibrant'."),
    (r"\bpivotal\b", "AI tell: 'pivotal'."),
    (r"\blandscape\b", "AI tell: 'landscape' (figurative use)."),
    (r"\bmeticulous\b", "AI tell: 'meticulous'."),
    (r"\bintricate\b", "AI tell: 'intricate'."),
    (r"\bfoster\b", "AI tell: 'foster' (figurative use)."),
    (r"\bshowcase\b", "AI tell: 'showcase'."),
    (r"\bunderscore\b", "AI tell: 'underscore' (figurative use)."),
    (r"\bbolster\b", "AI tell: 'bolster'."),
    (r"\bgarner\b", "AI tell: 'garner'."),
    (r"\benduring\b", "AI tell: 'enduring'."),
    (r"\bgroundbreaking\b", "AI tell: 'groundbreaking'."),
    (r"\brenowned\b", "AI tell: 'renowned'."),
    (r"\bprofound\b", "AI tell: 'profound'."),
    (r"\bexemplify\b", "AI tell: 'exemplify'."),
    (r"\bworld-class\b", "AI tell: promotional superlative 'world-class'."),
    (r"\btransformative\b", "AI tell: promotional superlative 'transformative'."),
    (r"\bgame-changing\b", "AI tell: promotional superlative 'game-changing'."),
    (r"\bcutting-edge\b", "AI tell: promotional superlative 'cutting-edge'."),
    (r"\brevolutionary\b", "AI tell: promotional superlative 'revolutionary'."),
    (r"\bbest-in-class\b", "AI tell: promotional superlative 'best-in-class'."),
    # Pattern tells.
    (r"\bnot just\b[^.!?]{1,60}\bbut\b", "AI tell: 'not just X, but Y' construction."),
    (r"\bcommitment to\b", "AI tell: 'commitment to' filler."),
    (r"\bvaluable insights\b", "AI tell: 'valuable insights' filler."),
]


def check_ai_writing(text: str, *, level: str = "warn") -> list[Violation]:
    """Detect AI writing tells. Default is warn-level; pass level='block' to hard-stop."""
    violations: list[Violation] = []
    for pat, rule in _AI_TELL_PATTERNS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            violations.append(
                Violation(
                    guard="ai_writing_tell",
                    severity=level,
                    snippet=text[max(0, m.start() - 20) : m.end() + 20],
                    rule=rule,
                )
            )
    return violations


# ── Model pin guard (no-Opus-on-API) ──────────────────────────────────────

# feedback_no_opus_on_api.md — Opus is banned on the Anthropic API empire-
# wide except in astromedha-v3/backend/app/engines/time_machine.py.
def check_no_opus_on_api(model: str, *, source_file: str = "") -> list[Violation]:
    """Verify a model ID is not Opus, with the documented time_machine exception.

    Pass `source_file` (a path or filename) to allow the time_machine bypass.
    """
    if "opus" not in model.lower():
        return []
    if "time_machine" in source_file:
        return []  # documented exception per feedback_no_opus_on_api.md
    return [
        Violation(
            guard="no_opus_on_api",
            severity="block",
            snippet=model,
            rule=(
                "Opus is banned on Anthropic API empire-wide. The single "
                "exception is astromedha-v3/backend/app/engines/time_machine.py."
            ),
        )
    ]


# ── Composite check ───────────────────────────────────────────────────────

CONTEXT_GENERAL = "general"
CONTEXT_KBK_REEL = "kbk_reel"
CONTEXT_KBK_CURTAIN = "kbk_curtain"

_VALID_CONTEXTS = {CONTEXT_GENERAL, CONTEXT_KBK_REEL, CONTEXT_KBK_CURTAIN}


def check_all(text: str, *, context: str = CONTEXT_GENERAL) -> list[Violation]:
    """Run the relevant guard suite for the given context.

    `context` selects which domain-specific checks run. AI-writing tells
    run on every context (warn-level); `kbk_reel` adds Sanganer + separator
    rules at block-level; `kbk_curtain` adds the curtain fact rules at
    block-level.
    """
    if context not in _VALID_CONTEXTS:
        raise ValueError(f"unknown copy_guards context: {context!r}")

    violations = check_ai_writing(text, level="warn")
    if context == CONTEXT_KBK_REEL:
        violations += check_kbk_reel(text)
    elif context == CONTEXT_KBK_CURTAIN:
        violations += check_kbk_curtain(text)
    return violations


def has_blocking(violations: list[Violation]) -> bool:
    """Convenience: True if any violation is severity 'block'."""
    return any(v.severity == "block" for v in violations)


def format_report(violations: list[Violation]) -> str:
    """Render violations as a human-readable report (for logs / errors)."""
    if not violations:
        return "no violations"
    lines = [f"{len(violations)} copy-guard violation(s):"]
    for v in violations:
        lines.append(f"  [{v.severity.upper()}] {v.guard}: {v.rule}")
        lines.append(f"      snippet: ...{v.snippet.strip()}...")
    return "\n".join(lines)
