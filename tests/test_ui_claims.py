"""Tests for empire.lint.ui_claims.

Pins the regression bar: the literal Anand hallucination ("update it on the
Profile page") MUST be flagged. Cross-framework: Next.js, Streamlit, static
HTML projects must all auto-discover surfaces correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empire.exceptions import UnverifiedUIClaim
from empire.lint.ui_claims import (
    LintResult,
    _claim_matches_surface,
    _looks_like_nav_label,
    discover_ui_surfaces,
    extract_ui_claims,
    lint_outbound_copy,
)


# ── Fixtures: synthetic frontend layouts ──────────────────


@pytest.fixture
def nextjs_root(tmp_path: Path) -> Path:
    """Minimal Next.js layout: src/app/<route>/page.tsx + a CardTitle."""
    app_dir = tmp_path / "src" / "app"
    (app_dir / "settings").mkdir(parents=True)
    (app_dir / "settings" / "page.tsx").write_text(
        '<CardTitle><Icon /> Birth Details</CardTitle>\n'
        '<CardTitle>Email Preferences</CardTitle>\n'
        '<h1>Welcome</h1>\n'
    )
    (app_dir / "dashboard").mkdir()
    (app_dir / "dashboard" / "page.tsx").write_text(
        '<h1>Dashboard</h1>\n'
    )
    return tmp_path


@pytest.fixture
def streamlit_root(tmp_path: Path) -> Path:
    """Minimal Streamlit layout: app.py + pages/."""
    (tmp_path / "app.py").write_text(
        'import streamlit as st\n'
        'st.set_page_config(page_title="Empire Dashboard")\n'
        'st.title("Daily Brief")\n'
        'st.sidebar.title("Settings")\n'
    )
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "1_Customer_Health.py").write_text('st.header("Customer Health")\n')
    return tmp_path


@pytest.fixture
def html_root(tmp_path: Path) -> Path:
    """Minimal static-HTML layout."""
    (tmp_path / "index.html").write_text(
        '<html><head><title>Moonpath</title></head>'
        '<body><h1>The Library</h1></body></html>'
    )
    (tmp_path / "library.html").write_text('<h1>Library</h1>')
    return tmp_path


# ── Surface discovery: per framework ──────────────────────


def test_nextjs_discovery_finds_routes(nextjs_root: Path):
    surfaces = discover_ui_surfaces(nextjs_root)
    lowered = {s.lower() for s in surfaces}
    assert "settings" in lowered
    assert "dashboard" in lowered


def test_nextjs_discovery_handles_icon_prefixed_card_titles(nextjs_root: Path):
    surfaces = discover_ui_surfaces(nextjs_root)
    lowered = {s.lower() for s in surfaces}
    assert "birth details" in lowered, (
        "Failed to extract 'Birth Details' from <CardTitle><Icon /> Birth Details</CardTitle>. "
        "Nested-tag stripping is broken."
    )


def test_streamlit_discovery_finds_page_title_and_pages(streamlit_root: Path):
    surfaces = discover_ui_surfaces(streamlit_root)
    lowered = {s.lower() for s in surfaces}
    assert "empire dashboard" in lowered
    assert "daily brief" in lowered
    assert "settings" in lowered  # st.sidebar.title
    assert "customer health" in lowered  # pages/1_Customer_Health.py


def test_html_discovery_finds_titles_and_filenames(html_root: Path):
    surfaces = discover_ui_surfaces(html_root)
    lowered = {s.lower() for s in surfaces}
    assert "moonpath" in lowered  # <title>
    assert "library" in lowered   # filename + h1


# ── Claim extraction ──────────────────────────────────────


def test_extract_page_suffix():
    assert "Profile" in extract_ui_claims("update it on the Profile page")


def test_extract_breadcrumb_arrow():
    claims = extract_ui_claims("Open Settings → Birth Details")
    assert "Settings" in claims
    assert "Birth Details" in claims


def test_extract_directional_does_not_greedy_grab():
    claims = extract_ui_claims("Click Submit to save.")
    assert "Submit" in claims
    assert "Submit to save" not in claims  # would be greedy IGNORECASE bug


def test_extract_skips_generic_words():
    claims = extract_ui_claims("Click here to go back.")
    assert not any(c.lower() in {"here", "back"} for c in claims)


def test_extract_dedupes():
    claims = extract_ui_claims("Open Settings. Then go to Settings again.")
    assert sum(1 for c in claims if c.lower() == "settings") == 1


# ── End-to-end: regression on the original incident ──────


def test_anand_hallucination_is_flagged(nextjs_root: Path):
    """The literal copy that shipped to a real customer 2026-04-25.

    "update it on the Profile page" — there is no Profile page. This test
    pins the linter so the same copy can never pass again.
    """
    bad = "<p>If you can find an exact birth time, update it on the Profile page.</p>"
    result = lint_outbound_copy(bad, nextjs_root)
    assert not result.ok
    assert "Profile" in result.unverified


def test_corrected_copy_passes(nextjs_root: Path):
    good = "<p>Open Settings -> Birth Details and update it there.</p>"
    result = lint_outbound_copy(good, nextjs_root)
    assert result.ok, f"unverified: {result.unverified}"


# ── Empty / no-op modes ──────────────────────────────────


def test_empty_text_passes(nextjs_root: Path):
    assert lint_outbound_copy("", nextjs_root).ok


def test_no_surfaces_no_frontend_fails_open():
    """When neither frontend_root nor surfaces is provided, fail open.

    Caller explicitly opted out of verification by passing nothing to compare
    against. Don't crash; just return ok=True.
    """
    result = lint_outbound_copy("Open the Profile page")
    assert result.ok
    assert result.surfaces == set()


def test_explicit_surfaces_override_discovery(tmp_path: Path):
    """Passing surfaces= bypasses auto-discovery entirely."""
    # tmp_path has no frontend at all. Pass an explicit allowlist.
    result = lint_outbound_copy(
        "Open Settings to update.",
        frontend_root=tmp_path,
        surfaces={"Settings"},
    )
    assert result.ok


# ── Exception integration ────────────────────────────────


def test_exception_carries_unverified_list():
    err = UnverifiedUIClaim(["Profile", "Garage"])
    assert err.unverified == ["Profile", "Garage"]
    assert "Profile" in str(err)
    assert "Garage" in str(err)


# ── Helper unit tests ────────────────────────────────────


def test_looks_like_nav_label_keeps_short_titlecase():
    assert _looks_like_nav_label("Birth Details")
    assert _looks_like_nav_label("Settings")


def test_looks_like_nav_label_rejects_prose():
    assert not _looks_like_nav_label("How the calculation works")
    assert not _looks_like_nav_label("Get your reading in under a minute")
    assert not _looks_like_nav_label("What you'll see")


def test_looks_like_nav_label_rejects_terminal_punctuation():
    assert not _looks_like_nav_label("Are you ready?")
    assert not _looks_like_nav_label("Welcome.")


def test_claim_matches_surface_strict():
    surfaces = {"Settings", "Birth Details"}
    assert _claim_matches_surface("Settings", surfaces)
    assert _claim_matches_surface("Birth Details", surfaces)
    assert not _claim_matches_surface("Profile", surfaces)


def test_claim_matches_surface_substring_does_not_falsely_pass():
    """The original v3 bug: 'Profile' matched 'Create your profile' via raw
    substring. Token-level match must reject that."""
    surfaces = {"Create your profile"}  # synthetic — even if heading prose leaked through
    assert not _claim_matches_surface("Profile", surfaces), (
        "Token-level match regressed — single-word claim is matching prose with "
        "the same word inside it. Bug from 2026-04-26 is back."
    )
