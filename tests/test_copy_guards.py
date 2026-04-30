"""Tests for empire.lint.copy_guards.

These tests pin the guard catalog to specific memory rules so a future
edit can't silently weaken a load-bearing check. Each test references the
feedback memory it codifies.
"""
from __future__ import annotations

import pytest

from empire.lint.copy_guards import (
    CONTEXT_GENERAL,
    CONTEXT_KBK_CURTAIN,
    CONTEXT_KBK_REEL,
    check_ai_writing,
    check_all,
    check_kbk_curtain,
    check_kbk_reel,
    check_no_opus_on_api,
    format_report,
    has_blocking,
)


# ── KBK reel guards ───────────────────────────────────────────────────────
# feedback_kbk_no_sanganer_in_social_copy.md
# feedback_kbk_reel_no_separators.md


def test_kbk_reel_blocks_sanganer():
    out = check_kbk_reel("Block-printed in Sanganer for 100 years")
    assert any(v.guard == "kbk_reel_no_sanganer" for v in out)
    assert all(v.severity == "block" for v in out if v.guard == "kbk_reel_no_sanganer")


def test_kbk_reel_sanganer_case_insensitive():
    out = check_kbk_reel("From SANGANER to your home")
    assert any(v.guard == "kbk_reel_no_sanganer" for v in out)


def test_kbk_reel_blocks_em_dash():
    out = check_kbk_reel("Block-printed — handmade")
    assert any(v.guard == "kbk_reel_no_separators" for v in out)


def test_kbk_reel_blocks_middle_dot():
    out = check_kbk_reel("Block-printed · handmade")
    assert any(v.guard == "kbk_reel_no_separators" for v in out)


def test_kbk_reel_allows_compound_hyphen():
    """`block-printed` is fine; only space-padded hyphens are banned."""
    out = check_kbk_reel("Block-printed handmade curtains")
    sep_violations = [v for v in out if v.guard == "kbk_reel_no_separators"]
    assert sep_violations == []


def test_kbk_reel_blocks_space_hyphen_glue():
    out = check_kbk_reel("Handmade - block-printed")
    assert any(v.guard == "kbk_reel_no_separators" for v in out)


def test_kbk_reel_pipe_separator_ok():
    """`|` is the approved line-break in reel overlays."""
    out = check_kbk_reel("Block-printed | handmade | from Jaipur")
    sep = [v for v in out if v.guard == "kbk_reel_no_separators"]
    assert sep == []


# ── KBK curtain copy guards ───────────────────────────────────────────────
# feedback_kbk_curtains_copy_facts.md


def test_kbk_curtain_blocks_natural_dye():
    out = check_kbk_curtain("Hand block-printed with natural dyes from Rajasthan")
    assert any(v.guard == "kbk_curtain_facts" for v in out)


def test_kbk_curtain_blocks_no_two_panels():
    out = check_kbk_curtain("No two panels are exactly the same")
    assert any(v.guard == "kbk_curtain_facts" for v in out)


def test_kbk_curtain_clean_copy_passes():
    out = check_kbk_curtain("Hand block-printed Kota cotton curtains, ships in 5-7 days")
    assert out == []


# ── AI writing tells ──────────────────────────────────────────────────────
# feedback_no_ai_writing.md


def test_ai_writing_flags_em_dash():
    out = check_ai_writing("This is meticulous — and intricate work")
    assert any("em-dash" in v.rule for v in out)
    assert any("meticulous" in v.rule for v in out)
    assert any("intricate" in v.rule for v in out)


def test_ai_writing_flags_not_just_but():
    out = check_ai_writing("This is not just a curtain, but a story.")
    assert any("not just" in v.rule.lower() for v in out)


def test_ai_writing_flags_promotional_superlatives():
    out = check_ai_writing("World-class transformative game-changing solution")
    rules = " ".join(v.rule for v in out)
    assert "world-class" in rules
    assert "transformative" in rules
    assert "game-changing" in rules


def test_ai_writing_default_severity_is_warn():
    out = check_ai_writing("This is groundbreaking work.")
    assert all(v.severity == "warn" for v in out)


def test_ai_writing_block_level():
    out = check_ai_writing("This is groundbreaking work.", level="block")
    assert all(v.severity == "block" for v in out)


def test_ai_writing_clean_copy_passes():
    out = check_ai_writing("Ships in 5 days. Free shipping. Cash on delivery.")
    assert out == []


# ── Opus-on-API guard ─────────────────────────────────────────────────────
# feedback_no_opus_on_api.md


def test_no_opus_blocks_opus_4_7():
    out = check_no_opus_on_api("claude-opus-4-7")
    assert len(out) == 1
    assert out[0].severity == "block"


def test_no_opus_blocks_opus_4_6():
    out = check_no_opus_on_api("claude-opus-4-6")
    assert len(out) == 1


def test_no_opus_allows_sonnet():
    assert check_no_opus_on_api("claude-sonnet-4-6") == []


def test_no_opus_allows_haiku():
    assert check_no_opus_on_api("claude-haiku-4-5-20251001") == []


def test_no_opus_allows_time_machine_exception():
    """The single documented exception per feedback_no_opus_on_api.md."""
    out = check_no_opus_on_api(
        "claude-opus-4-7",
        source_file="astromedha-v3/backend/app/engines/time_machine.py",
    )
    assert out == []


def test_no_opus_blocks_other_files():
    """Only time_machine is exempt — other engines must use Sonnet."""
    out = check_no_opus_on_api(
        "claude-opus-4-7",
        source_file="astromedha-v3/backend/app/engines/morning_briefing.py",
    )
    assert len(out) == 1


# ── Composite check_all ───────────────────────────────────────────────────


def test_check_all_general_runs_ai_writing_only():
    out = check_all("Block-printed handmade with Sanganer", context=CONTEXT_GENERAL)
    # General context does NOT trigger the kbk_reel sanganer rule.
    assert all(v.guard != "kbk_reel_no_sanganer" for v in out)


def test_check_all_kbk_reel_combines_rules():
    out = check_all("Sanganer block-printed — handmade", context=CONTEXT_KBK_REEL)
    guards = {v.guard for v in out}
    assert "kbk_reel_no_sanganer" in guards
    assert "kbk_reel_no_separators" in guards
    assert "ai_writing_tell" in guards  # em-dash still flagged


def test_check_all_kbk_curtain_combines_rules():
    out = check_all("Natural-dye showcase quality", context=CONTEXT_KBK_CURTAIN)
    guards = {v.guard for v in out}
    assert "kbk_curtain_facts" in guards


def test_check_all_rejects_unknown_context():
    with pytest.raises(ValueError):
        check_all("text", context="nonexistent")


# ── Helpers ──────────────────────────────────────────────────────────────


def test_has_blocking_true_when_block_severity_present():
    out = check_kbk_reel("From Sanganer with love")
    assert has_blocking(out) is True


def test_has_blocking_false_for_warn_only():
    out = check_ai_writing("This is groundbreaking work.")  # warn-level
    assert has_blocking(out) is False


def test_format_report_no_violations():
    assert format_report([]) == "no violations"


def test_format_report_includes_severity_and_rule():
    out = check_kbk_reel("From Sanganer")
    rep = format_report(out)
    assert "BLOCK" in rep
    assert "kbk_reel_no_sanganer" in rep
    assert "Sanganer" in rep
