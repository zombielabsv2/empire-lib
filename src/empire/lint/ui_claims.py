"""Pre-send linter for user-facing copy that references UI surfaces.

The 2026-04-26 incident that birthed this module: an outbound apology email
told a real customer "update it on the Profile page" — a surface that did not
and never did exist. He went looking, found nothing, DM'd Rahul. Schema
support (the `birth_hour` column existed) is NOT proof of UI support.

Memory rules are advice for interactive sessions; this is the code-level
enforcement so autonomous agents (drip emails, contribution emails, inbox
bots, support replies) can't ship the same fabrication.

Multi-framework discovery:

- Next.js: routes from src/app/<dir>/page.tsx + headings inside <CardTitle>,
  <h1>..<h4>, <DialogTitle>, <TabsTrigger>.
- Streamlit: page titles from st.set_page_config(page_title=...), st.title(),
  st.header(), st.subheader(), st.sidebar.title(), and pages/<file>.py
  filenames.
- Static HTML: <title>, <h1>, <h2>, <h3> from *.html files at project root,
  plus the *.html filenames themselves.

Caller can also pass an explicit `surfaces` set (overrides auto-discovery)
when the project doesn't fit any of the above shapes.

Usage:

    from empire.lint.ui_claims import lint_outbound_copy
    from empire.exceptions import UnverifiedUIClaim

    result = lint_outbound_copy(
        text=email_html,
        frontend_root=Path("/path/to/frontend"),
    )
    if not result.ok:
        raise UnverifiedUIClaim(result.unverified)

CLI:
    python -m empire.lint.ui_claims --frontend ./frontend draft.html
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Public types ───────────────────────────────────────────


@dataclass
class LintResult:
    """Result of a single lint pass.

    Attributes:
        ok: True iff every UI claim in the copy maps to a real surface.
        claimed: All UI surface names extracted from the copy (de-duped, order
                 preserved).
        unverified: Subset of `claimed` that did not match any known surface.
                    Empty when ok is True.
        surfaces: The full surface set discovered from the frontend / passed in.
                  Useful for debugging false negatives.
    """

    ok: bool
    claimed: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    surfaces: set[str] = field(default_factory=set)

    def report(self) -> str:
        if self.ok:
            return f"ui_claim_linter: PASS ({len(self.claimed)} claims, all verified)"
        lines = [
            f"ui_claim_linter: FAIL ({len(self.unverified)} unverified claims)",
            "",
            "Unverified UI surfaces (mentioned in copy but not found in frontend):",
        ]
        lines.extend(f"  - {c!r}" for c in self.unverified)
        lines.append("")
        lines.append(
            "Either: (a) the surface really exists, in which case add the literal "
            "string to a heading/route/page-title; or (b) the surface doesn't exist, "
            "in which case rewrite the copy."
        )
        return "\n".join(lines)


# ── Surface discovery ──────────────────────────────────────


_SKIP_DIRS = {"node_modules", "__pycache__", ".next", "dist", "build", ".turbo", ".venv", "venv", ".pytest_cache"}
_SKIP_ROUTE_PARENTS = {"app", "api", "src"}

# Next.js JSX headings — covers icon-prefixed titles via _strip_jsx
_JSX_HEADING_RE = re.compile(
    r"<(CardTitle|h[1-4]|DialogTitle|TabsTrigger)\b[^>]*>"
    r"(.*?)"
    r"</\1>",
    re.DOTALL,
)
_JSX_TAG_RE = re.compile(r"<[^>]*>")
_JSX_EXPR_RE = re.compile(r"\{[^{}]*\}")
_WS_RE = re.compile(r"\s+")
_NAV_LABEL_RE = re.compile(
    r'(?:label|title|aria-label)\s*[:=]\s*"([A-Z][^"\n]{1,60}?)"'
)

# Streamlit calls
_ST_PAGE_TITLE_RE = re.compile(r'page_title\s*=\s*["\']([^"\']{2,60})["\']')
_ST_HEADING_RE = re.compile(
    r'st\.(?:title|header|subheader|sidebar\.title|sidebar\.header|sidebar\.subheader)'
    r'\(\s*["\']([^"\']{2,80})["\']'
)

# Static HTML
_HTML_TITLE_RE = re.compile(r"<title>([^<]{2,80})</title>", re.IGNORECASE)
_HTML_HEADING_RE = re.compile(
    r"<(h[1-3])\b[^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)


def discover_ui_surfaces(frontend_root: Path) -> set[str]:
    """Walk a project tree and collect strings that name real UI surfaces.

    Auto-detects framework: looks for src/app/ (Next.js), app.py / pages/
    (Streamlit), or *.html at project root (static HTML).

    Returns a permissive superset. False positives in the surface set just
    mean fewer flags from the linter (one-way: fail-open on coverage,
    fail-closed on missing claims). The asymmetry is right because a missed
    flag costs a customer; a spurious flag costs five minutes.
    """
    surfaces: set[str] = set()

    # ── Next.js: file-based routing ────────────────────────
    app_dir = frontend_root / "src" / "app"
    if app_dir.exists():
        for page_file in app_dir.rglob("page.tsx"):
            # Only the route segments between app/ and page.tsx count.
            # Walking page_file.parts would also pick up tmp/Users/AppData
            # from absolute paths.
            rel_parts = page_file.relative_to(app_dir).parts[:-1]
            for part in rel_parts:
                if part.startswith("(") or part.startswith("["):
                    continue
                _add_if_label(surfaces, part.replace("-", " ").replace("_", " "))

    # ── Next.js: JSX headings + nav labels ─────────────────
    src_dir = frontend_root / "src"
    if src_dir.exists():
        for tsx in src_dir.rglob("*.tsx"):
            if any(skip in tsx.parts for skip in _SKIP_DIRS):
                continue
            content = _safe_read(tsx)
            for match in _JSX_HEADING_RE.finditer(content):
                _add_if_label(surfaces, _strip_jsx(match.group(2)))
            for match in _NAV_LABEL_RE.finditer(content):
                _add_if_label(surfaces, match.group(1).strip())

    # ── Streamlit: app.py + pages/ ─────────────────────────
    streamlit_files: list[Path] = []
    if (frontend_root / "app.py").exists():
        streamlit_files.append(frontend_root / "app.py")
    if (frontend_root / "pages").is_dir():
        streamlit_files.extend((frontend_root / "pages").glob("*.py"))

    for py in streamlit_files:
        content = _safe_read(py)
        for match in _ST_PAGE_TITLE_RE.finditer(content):
            _add_if_label(surfaces, match.group(1).strip())
        for match in _ST_HEADING_RE.finditer(content):
            _add_if_label(surfaces, match.group(1).strip())
        # The pages/<filename>.py becomes a sidebar entry on the deployed app
        if py.parent.name == "pages":
            stem = py.stem
            # Streamlit strips leading digits + underscores
            cleaned = re.sub(r"^\d+_", "", stem).replace("_", " ").strip()
            _add_if_label(surfaces, cleaned)

    # ── Static HTML: *.html at root + first level ──────────
    if frontend_root.exists():
        for html in frontend_root.glob("*.html"):
            _add_html_surfaces(surfaces, html)
        # one level deep
        for sub in frontend_root.iterdir():
            if sub.is_dir() and sub.name not in _SKIP_DIRS:
                for html in sub.glob("*.html"):
                    _add_html_surfaces(surfaces, html)

    return surfaces


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _strip_jsx(raw: str) -> str:
    cleaned = _JSX_TAG_RE.sub(" ", raw)
    cleaned = _JSX_EXPR_RE.sub(" ", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def _add_html_surfaces(surfaces: set[str], html: Path) -> None:
    content = _safe_read(html)
    for match in _HTML_TITLE_RE.finditer(content):
        _add_if_label(surfaces, match.group(1).strip())
    for match in _HTML_HEADING_RE.finditer(content):
        _add_if_label(surfaces, _strip_jsx(match.group(2)))
    # Filename itself ("library.html" -> "Library")
    stem = html.stem.replace("-", " ").replace("_", " ").strip()
    _add_if_label(surfaces, stem)


def _add_if_label(surfaces: set[str], text: str) -> None:
    if not text:
        return
    text = text.strip()
    if not text:
        return
    # Route directory names arrive lowercase ("settings"). Title-case before
    # the label check so they pass the "must start uppercase" rule, then add
    # all three case variants so claim matching works regardless of how the
    # outbound copy spells it.
    candidate = text if text[0].isupper() else text.title()
    if not _looks_like_nav_label(candidate):
        return
    surfaces.add(candidate)
    surfaces.add(candidate.title())
    surfaces.add(candidate.lower())


_PROSE_WORDS = {
    "how", "what", "why", "where", "when", "you", "your", "you'll", "you're",
    "we", "our", "us", "i", "are", "is", "was", "be", "been", "for", "to",
    "of", "with", "in", "on", "at", "by", "from", "and", "or", "but",
    "the", "a", "an", "this", "that", "if", "then", "than", "as", "so",
}


def _looks_like_nav_label(text: str) -> bool:
    """Filter heading text down to things that plausibly name a UI surface.

    Reject prose ("Get your reading in under a minute"), descriptive sentences
    (terminal punctuation), and headings with too many words. Keep title-cased
    short phrases ("Birth Details", "Email Preferences", "Daily Guidance").
    """
    if not text or len(text) > 50:
        return False
    if text[-1] in ".?!":
        return False
    words = text.split()
    if not words or len(words) > 5:
        return False
    if not text[0].isupper() and not text[0].isdigit():
        return False
    lowered = {w.lower().rstrip(",.;:") for w in words}
    if lowered & _PROSE_WORDS:
        return False
    return True


# ── Claim extraction ───────────────────────────────────────

# Strict capitalised noun phrase: each token must start with an uppercase
# letter. Used as the *target* of a UI claim — case must not drift via
# re.IGNORECASE on this group, otherwise "Click Submit to save" greedy-grabs
# "Submit to save".
_NOUN_PHRASE = r"[A-Z][a-zA-Z]{1,20}(?:\s+[A-Z][a-zA-Z]{1,20}){0,3}"

_THE_OR_YOUR = r"(?:[Tt]he|[Yy]our)"
_DIRECTIONAL_VERBS = (
    r"(?:[Oo]pen|[Gg]o\s+to|[Nn]avigate\s+to|[Cc]lick(?:\s+on)?|"
    r"[Tt]ap(?:\s+on)?|[Hh]ead\s+(?:over\s+)?to|[Vv]isit)"
)

_PAGE_REF_RE = re.compile(
    rf"\b{_THE_OR_YOUR}\s+({_NOUN_PHRASE})\s+(page|tab|menu|button|section|screen|card|dialog|modal)\b"
)
_DIRECTIONAL_RE = re.compile(
    rf"\b{_DIRECTIONAL_VERBS}\s+(?:{_THE_OR_YOUR}\s+)?({_NOUN_PHRASE})"
)
_BREADCRUMB_RE = re.compile(
    rf"\b({_NOUN_PHRASE})\s*(?:→|->|&rarr;|&gt;|>)\s*({_NOUN_PHRASE})\b"
)

_GENERIC_BLOCKLIST = {
    "the", "your", "a", "an", "this", "that", "it", "here", "there",
    "page", "tab", "menu", "button", "section", "screen",
    "today", "tomorrow", "yesterday", "now", "above", "below", "next", "previous",
    "home", "back", "forward",
    "email", "emails", "inbox", "spam",
    "wifi", "internet", "browser", "phone", "computer", "laptop", "device",
    "google", "gmail", "chrome", "safari", "firefox", "edge",
    "india", "delhi", "mumbai", "bangalore",
}


def extract_ui_claims(text: str) -> list[str]:
    """Pull out phrases from `text` that imply a UI surface exists.

    Returns the captured noun phrases (e.g. "Profile" from "the Profile page",
    "Birth Details" from "Settings → Birth Details"). De-duplicated, case
    preserved, order preserved.
    """
    claims: list[str] = []
    seen: set[str] = set()

    def add(claim: str) -> None:
        cleaned = claim.strip(" .,;:!?\"'")
        if not cleaned or len(cleaned) < 2:
            return
        if cleaned.lower() in _GENERIC_BLOCKLIST:
            return
        if " " not in cleaned and cleaned[:1].islower():
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        claims.append(cleaned)

    for match in _PAGE_REF_RE.finditer(text):
        add(match.group(1))
    for match in _DIRECTIONAL_RE.finditer(text):
        add(match.group(1))
    for match in _BREADCRUMB_RE.finditer(text):
        add(match.group(1))
        add(match.group(2))

    return claims


# ── Verification ───────────────────────────────────────────


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _claim_matches_surface(claim: str, surfaces: set[str]) -> bool:
    """A claim is verified iff its tokens are an exact match for, a *prefix* of,
    or contain as a *suffix*, the tokens of any known surface.

    This is biased toward "claim is the head of the surface name" — the way
    humans actually navigate. You say "the Settings page" when the surface is
    "Settings & Privacy" (claim is prefix). You say "open Settings" when the
    surface is just "Settings" (surface is suffix of the verb-prefixed claim).

    The biased rule rejects single-word claims that happen to appear *inside*
    multi-word prose ("Profile" inside "Create your profile") — that match
    direction was the whole 2026-04-26 incident.
    """
    claim_tokens = tuple(_normalize(claim).split())
    if not claim_tokens:
        return True
    claim_len = len(claim_tokens)
    for surface in surfaces:
        s_tokens = tuple(_normalize(surface).split())
        if not s_tokens:
            continue
        if claim_tokens == s_tokens:
            return True
        if claim_len < len(s_tokens):
            # Claim must be the *prefix* of surface tokens.
            if s_tokens[:claim_len] == claim_tokens:
                return True
        else:  # claim_len > len(s_tokens)
            # Surface must be the *suffix* of claim tokens (handles
            # "Open Settings" matching surface "Settings").
            if claim_tokens[-len(s_tokens):] == s_tokens:
                return True
    return False


def lint_outbound_copy(
    text: str,
    frontend_root: Path | None = None,
    *,
    surfaces: set[str] | None = None,
) -> LintResult:
    """Lint outbound user-facing copy against the live frontend repo.

    Args:
        text: The outbound copy. Plain text or HTML — both work.
        frontend_root: Filesystem path to the project's frontend root. The
                       linter will auto-discover surfaces from Next.js
                       (src/app + .tsx), Streamlit (app.py + pages/), or
                       static HTML. Pass None if you provide `surfaces`
                       explicitly.
        surfaces: Explicit allowlist of UI surface names. When set, replaces
                  auto-discovery entirely. Useful for projects with custom
                  frontends or when supplementing auto-discovery.

    Returns:
        LintResult with `ok=False` if any UI claim could not be matched to a
        real surface.
    """
    if surfaces is None:
        if frontend_root is None:
            # Nothing to verify against — fail open. Caller wanted this.
            claims = extract_ui_claims(text)
            return LintResult(ok=True, claimed=claims, unverified=[], surfaces=set())
        surfaces = discover_ui_surfaces(frontend_root)

    claims = extract_ui_claims(text)
    unverified = [c for c in claims if not _claim_matches_surface(c, surfaces)]
    return LintResult(
        ok=not unverified,
        claimed=claims,
        unverified=unverified,
        surfaces=surfaces,
    )


# ── CLI ────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lint outbound copy for fabricated UI surface references. "
            "Exits non-zero on unverified claims."
        )
    )
    parser.add_argument("files", nargs="*", help="Files to lint. Reads stdin if omitted.")
    parser.add_argument("--frontend", required=True, help="Path to the project's frontend root.")
    args = parser.parse_args()

    frontend_root = Path(args.frontend).resolve()
    if not frontend_root.exists():
        print(f"error: {frontend_root} does not exist", file=sys.stderr)
        return 2

    if args.files:
        any_failed = False
        for path in args.files:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            result = lint_outbound_copy(text, frontend_root)
            print(f"== {path} ==")
            print(result.report())
            if not result.ok:
                any_failed = True
        return 1 if any_failed else 0

    text = sys.stdin.read()
    result = lint_outbound_copy(text, frontend_root)
    print(result.report())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
