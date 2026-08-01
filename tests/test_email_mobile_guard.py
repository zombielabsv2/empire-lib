"""The phone-safety guard on send_email_tracked (2026-08-02).

This sender posts straight to api.resend.com by design — it is the one path in
the empire that survives a `resend-send` edge-function outage — which means
nothing downstream ever makes its HTML phone-safe. Before this guard existed, a
hand-built email went out through it with no phone check of any kind behind it.

The guard DETECTS, it does not rewrite. The rewriter half (`mobile_safe()`) has
caused two production rendering incidents of its own, so nothing here is
allowed to touch the author's markup.
"""
from __future__ import annotations

import pytest

from empire.email.mobile import (
    NOWRAP_SAFE_CHARS,
    PHONE_SAFE_PX,
    EmailNotPhoneSafe,
    assert_phone_safe,
    find_overflow_risks,
)

SAFE = (
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<div style="overflow-wrap:break-word">'
    '<table width="100%" style="max-width:640px"><tr>'
    "<td>Locality</td><td>Dir</td><td>km</td>"
    "</tr></table></div>"
)


def test_a_phone_safe_email_has_no_risks():
    assert find_overflow_risks(SAFE) == []
    assert_phone_safe(SAFE)  # must not raise


@pytest.mark.parametrize("html,needle", [
    ('<div style="width:700px">x</div>', "fixed inline width:700px"),
    ('<table width="640"><tr><td>x</td></tr></table>', "width=640"),
    ('<div style="min-width:520px">x</div>', "min-width:520px"),
    ('<table width="500"><tr><td>x</td></tr></table>', "no <meta viewport>"),
])
def test_each_overflow_shape_is_caught(html, needle):
    assert any(needle in r for r in find_overflow_risks(html)), find_overflow_risks(html)


def test_duplicate_style_attribute_is_caught():
    """Parsers keep the FIRST style= and discard the rest, so whichever half
    held the background or border is silently gone. A real 2026-07-26 bug."""
    html = SAFE + '<td style="color:#fff" style="background:#000">x</td>'
    assert any("duplicate style=" in r for r in find_overflow_risks(html))


def test_short_nowrap_is_allowed_but_a_long_run_is_not():
    """nowrap on a short numeric cell is the FIX (it stops 'Rs 33,083'
    shattering); it is only a bug when the un-wrappable run is long."""
    short = SAFE + '<td style="white-space:nowrap">Rs 33,083</td>'
    assert not any("nowrap" in r for r in find_overflow_risks(short))

    long_run = "y" * (NOWRAP_SAFE_CHARS + 5)
    long_cell = SAFE + f'<td style="white-space:nowrap">{long_run}</td>'
    assert any("nowrap" in r for r in find_overflow_risks(long_cell))


def test_a_bare_paragraph_is_not_flagged_for_a_missing_viewport():
    """A plain email has no layout to scale, so demanding a viewport meta on it
    is a false positive — and a guard that cries wolf gets switched off. The
    check only applies once the email carries a table or a pinned width."""
    assert find_overflow_risks("<p>hello</p>") == []
    assert find_overflow_risks("<div><b>hi</b> there</div>") == []
    # ...but the moment there IS layout, the meta is required again.
    assert any("viewport" in r for r in
               find_overflow_risks('<table width="500"><tr><td>x</td></tr></table>'))


def test_wide_data_table_needs_a_scroll_container():
    four_col = (
        '<meta name="viewport" content="width=device-width">'
        "<table><tr><td>a</td><td>b</td><td>c</td><td>d</td></tr></table>"
    )
    assert any("4-column" in r for r in find_overflow_risks(four_col))


def test_assert_phone_safe_raises_and_carries_every_reason():
    bad = '<table width="640" style="width:700px"><tr><td>x</td></tr></table>'
    with pytest.raises(EmailNotPhoneSafe) as ei:
        assert_phone_safe(bad)
    assert len(ei.value.risks) >= 3
    assert str(PHONE_SAFE_PX) in str(ei.value)


def test_the_sender_runs_the_guard_before_it_needs_a_key(monkeypatch):
    """The guard must fire BEFORE the Resend key is resolved, so a bad email
    fails identically whether or not the caller has credentials."""
    from empire.email.sender import send_email_tracked

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(EmailNotPhoneSafe):
        send_email_tracked(
            to="a@b.com", subject="s",
            html='<div style="width:900px">x</div>',
            user_id="u", profile_person_key="p",
        )


def test_allow_overflow_risks_is_the_only_way_past(monkeypatch):
    """The escape hatch exists, and taking it gets you to the NEXT gate (the
    missing key) rather than silently sending."""
    from empire.exceptions import ResendKeyMissing
    from empire.email.sender import send_email_tracked

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(ResendKeyMissing):
        send_email_tracked(
            to="a@b.com", subject="s",
            html='<div style="width:900px">x</div>',
            user_id="u", profile_person_key="p",
            allow_overflow_risks=True,
        )
