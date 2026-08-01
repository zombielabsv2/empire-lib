"""Phone-safety guard for outbound HTML email.

`send_email_tracked` posts straight to api.resend.com, deliberately: it is the
one send path in the empire that does NOT depend on the `resend-send` Supabase
edge function, so there is always a way to send mail when that layer is the
thing that is broken. The cost of that independence is that empire-lib emails
never get the edge function's mobile-safe rewrite.

This module buys the safety back without giving up the independent path, and
without machine-editing the author's HTML: it only *detects*. An email that
would clip on a phone raises before it is sent, so the failure lands on the
author instead of on the reader.

ORIGIN AND DRIFT WARNING
------------------------
This is a second Python copy of the guard half of
`~/kari-growth-platform/utils/email_mobile.py` (which also holds `mobile_safe()`,
the rewriter, mirrored again in TypeScript in the `resend-send` edge function).
Two implementations can drift, and that drift is itself the bug this family of
checks exists to prevent.

It was copied rather than shared because KBK pins `empire-lib @ v0.1.0` in its
requirements, so its Cloud Run image cannot import a symbol added here until
that pin is bumped and a tag cut. The unification path, when someone takes it:
bump KBK's pin, then have `utils/email_mobile.py` re-export from this module and
delete its local copy, leaving one Python implementation.

Both incidents encoded in the thresholds below are real and are documented in
`reference_empire_email_mobile_safe_chokepoint`.
"""
from __future__ import annotations

import re

# A phone viewport at its narrowest common width. Anything pinned wider than
# this pushes the body out and the reader gets a horizontally-scrolling email.
PHONE_SAFE_PX = 360

# white-space:nowrap on a SHORT cell is the fix, not the bug — it stops
# "Rs 33,083" shattering into "Rs 33,0 / 83". It only becomes dangerous when the
# un-wrappable run is longer than a phone column can hold.
NOWRAP_SAFE_CHARS = 28

_INLINE_WIDTH = re.compile(r"(?<!max-)(?<!min-)width\s*:\s*(\d{3,})px", re.I)


class EmailNotPhoneSafe(ValueError):
    """Raised pre-send when an email would clip on a phone."""

    def __init__(self, risks: list[str]) -> None:
        self.risks = risks
        joined = "\n  - ".join(risks)
        super().__init__(
            f"email would clip on a {PHONE_SAFE_PX}px phone:\n  - {joined}\n"
            "Fix the HTML, or pass allow_overflow_risks=True if this is "
            "deliberate and the recipient reads on desktop."
        )


def _nowrap_cell_texts(html: str) -> list[str]:
    """Visible text of every element whose style pins white-space:nowrap.

    Grab the tag carrying the nowrap, read to the matching close of that same
    tag name, then strip inner tags and entities so only on-screen characters
    count.
    """
    texts: list[str] = []
    for m in re.finditer(
        r'<(\w+)[^>]*\bstyle=["\'][^"\']*white-space\s*:\s*nowrap[^"\']*["\'][^>]*>',
        html, re.I,
    ):
        tag = m.group(1)
        close = re.search(rf"</{tag}\b", html[m.end():], re.I)
        inner = html[m.end():m.end() + close.start()] if close else html[m.end():m.end() + 200]
        text = re.sub(r"<[^>]+>", "", inner)           # drop nested tags
        text = re.sub(r"&[a-z]+;|&#\d+;", "x", text)   # each entity is ~1 glyph
        texts.append(re.sub(r"\s+", " ", text).strip())
    return texts


def find_overflow_risks(html: str) -> list[str]:
    """Return the reasons this email can clip on a phone. Empty list = safe."""
    risks: list[str] = []

    for px in sorted({int(x) for x in _INLINE_WIDTH.findall(html)}):
        if px > PHONE_SAFE_PX:
            risks.append(f"fixed inline width:{px}px (> {PHONE_SAFE_PX}px phone width)")

    for m in re.finditer(r'<(table|td|th|img)[^>]*?\swidth=["\']?(\d{3,})', html, re.I):
        px = int(m.group(2))
        if px > PHONE_SAFE_PX:
            risks.append(f"<{m.group(1).lower()} width={px}> attribute (> {PHONE_SAFE_PX}px)")

    for px in sorted({int(x) for x in re.findall(r"min-width\s*:\s*(\d{3,})px", html, re.I)}):
        if px > PHONE_SAFE_PX:
            risks.append(f"min-width:{px}px pins the layout wider than a phone")

    # A missing viewport only matters when there is layout to scale. A bare
    # <p> cannot clip on a phone, and flagging it would make the guard cry wolf
    # on every plain ad-hoc email — which is how a guard gets switched off. So
    # require the meta only once the email carries real layout.
    has_layout = bool(
        re.search(r"<table\b", html, re.I)
        or re.search(r"(?:max-|min-)?width\s*[:=]\s*[\"']?\d{3,}", html, re.I)
    )
    if has_layout and not re.search(r'name=["\']viewport', html, re.I):
        risks.append("no <meta viewport> — mobile clients won't scale the email")

    if not re.search(r"overflow-wrap|word-break", html, re.I):
        long_url = max((len(t) for t in re.findall(r"https?://\S+", html)), default=0)
        if long_url > 60:
            risks.append(f"{long_url}-char URL with no word-break — widens the body on its own")

    for txt in _nowrap_cell_texts(html):
        if len(txt) > NOWRAP_SAFE_CHARS:
            risks.append(
                f"white-space:nowrap on a {len(txt)}-char run "
                f"(> {NOWRAP_SAFE_CHARS}) — it can't wrap and widens the body"
            )
            break

    for m in re.finditer(r"<table\b.*?</table>", html, re.S | re.I):
        first_row = re.search(r"<tr\b.*?</tr>", m.group(0), re.S | re.I)
        cells = len(re.findall(r"<t[dh]\b", first_row.group(0), re.I)) if first_row else 0
        if cells >= 4:
            before = html[max(0, m.start() - 60):m.start()]
            if "kbk-scroll" not in before:
                risks.append(f"{cells}-column data table not in a .kbk-scroll container")

    # A tag with two style attributes is a silent styling loss, not a syntax nit:
    # parsers keep the first and discard the rest, so whichever half held the
    # background / border / width is simply gone. (Rahul, 2026-07-26.)
    for m in re.finditer(r"<[a-z][^>]*>", html, re.I):
        if len(re.findall(r"\sstyle\s*=", m.group(0), re.I)) > 1:
            risks.append(
                f"duplicate style= on <{m.group(0).split()[0].lstrip('<').lower()}> — "
                "clients keep the first and discard the rest"
            )
            break

    return sorted(set(risks))


def assert_phone_safe(html: str) -> None:
    """Raise EmailNotPhoneSafe if this email would clip on a phone."""
    risks = find_overflow_risks(html)
    if risks:
        raise EmailNotPhoneSafe(risks)
