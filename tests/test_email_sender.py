"""Tests for empire.email.sender."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from empire.config import supabase_creds
from empire.email import sender
from empire.email.sender import send_email_tracked
from empire.exceptions import (
    CopyGuardViolation,
    EmailLogPersistFailed,
    MissingTrackingContext,
    ResendKeyMissing,
    UnverifiedUIClaim,
)


def _mock_resp(status: int, json_data: dict | None = None,
               text: str = "") -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.json.return_value = json_data or {}
    r.text = text or (str(json_data) if json_data else "")
    if status >= 400:
        def _raise():
            raise httpx.HTTPStatusError(
                f"HTTP {status}", request=MagicMock(), response=r,
            )
        r.raise_for_status.side_effect = _raise
    else:
        r.raise_for_status.return_value = None
    return r


def test_requires_user_id(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    with pytest.raises(MissingTrackingContext):
        send_email_tracked(
            to="x@example.com", subject="s", html="<p>h</p>",
            user_id="", profile_person_key="kairav",
        )


def test_requires_profile_person_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    with pytest.raises(MissingTrackingContext):
        send_email_tracked(
            to="x@example.com", subject="s", html="<p>h</p>",
            user_id="u123", profile_person_key="",
        )


def test_requires_to(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    with pytest.raises(MissingTrackingContext):
        send_email_tracked(
            to="", subject="s", html="<p>h</p>",
            user_id="u", profile_person_key="k",
        )


def test_raises_when_resend_key_missing(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(ResendKeyMissing):
        send_email_tracked(
            to="x@example.com", subject="s", html="<p>h</p>",
            user_id="u", profile_person_key="k",
        )


def test_kwargs_only_signature(monkeypatch):
    """Calling with positional args must fail with TypeError."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    with pytest.raises(TypeError):
        send_email_tracked("x@example.com", "s", "<p>h</p>",  # type: ignore[misc]
                           "u", "k")


def test_happy_path(monkeypatch):
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")

    posts: list[dict] = []

    def fake_post(url, **kwargs):
        posts.append({"url": url, "json": kwargs.get("json"),
                      "headers": kwargs.get("headers")})
        if "resend.com" in url:
            return _mock_resp(200, {"id": "rsnd_123"})
        return _mock_resp(201, {})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        result = send_email_tracked(
            to="user@example.com",
            subject="Daily guidance",
            html="<p>hello</p>",
            user_id="user_abc",
            profile_person_key="kairav",
        )

    assert result["id"] == "rsnd_123"
    assert len(posts) == 2
    assert "resend.com" in posts[0]["url"]
    assert posts[0]["json"]["to"] == "user@example.com"
    assert posts[0]["json"]["from"] == "noreply@rxjapps.in"
    assert posts[1]["url"].endswith("/rest/v1/email_log")
    assert posts[1]["json"]["resend_id"] == "rsnd_123"
    assert posts[1]["json"]["user_id"] == "user_abc"
    assert posts[1]["json"]["profile_person_key"] == "kairav"
    supabase_creds.reset_cache()


def test_email_log_failure_raises_with_resend_id(monkeypatch, capsys):
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")

    def fake_post(url, **kwargs):
        if "resend.com" in url:
            return _mock_resp(200, {"id": "rsnd_456"})
        # email_log insert returns 500
        return _mock_resp(500, text="boom")

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        with pytest.raises(EmailLogPersistFailed) as exc_info:
            send_email_tracked(
                to="x@example.com", subject="s", html="<p>h</p>",
                user_id="u", profile_person_key="k",
            )

    assert exc_info.value.resend_id == "rsnd_456"
    captured = capsys.readouterr()
    assert "tracking was lost" in captured.err or "rsnd_456" in captured.err
    supabase_creds.reset_cache()


def test_resend_non_2xx_propagates(monkeypatch):
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")

    def fake_post(url, **kwargs):
        return _mock_resp(401, text="unauthorized")

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        with pytest.raises(httpx.HTTPStatusError):
            send_email_tracked(
                to="x@example.com", subject="s", html="<p>h</p>",
                user_id="u", profile_person_key="k",
            )
    supabase_creds.reset_cache()


def test_reply_to_passed_through(monkeypatch):
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")

    captured: dict = {}

    def fake_post(url, **kwargs):
        if "resend.com" in url:
            captured["resend_body"] = kwargs.get("json")
            return _mock_resp(200, {"id": "rsnd_789"})
        return _mock_resp(201, {})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        send_email_tracked(
            to="x@example.com", subject="s", html="<p>h</p>",
            user_id="u", profile_person_key="k",
            reply_to="rahul@example.com",
        )
    assert captured["resend_body"]["reply_to"] == "rahul@example.com"
    supabase_creds.reset_cache()


def test_ui_claim_lint_blocks_send(monkeypatch, tmp_path):
    """Hard-stop when html body references a UI surface not in the frontend.

    Regression: 2026-04-26 incident — autonomous email pointed customer at
    a non-existent "Profile page". Linter prevents the send entirely (not
    just warns), so no resend.com call should fire.
    """
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    # Synthetic Next.js frontend: only Settings exists, no Profile.
    app_dir = tmp_path / "src" / "app" / "settings"
    app_dir.mkdir(parents=True)
    (app_dir / "page.tsx").write_text("<h1>Settings</h1>")

    fired: list[str] = []

    def fake_post(url, **_kwargs):
        fired.append(url)
        return _mock_resp(200, {"id": "should_not_happen"})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        with pytest.raises(UnverifiedUIClaim) as exc_info:
            send_email_tracked(
                to="x@example.com",
                subject="s",
                html="<p>Update it on the Profile page.</p>",
                user_id="u", profile_person_key="k",
                frontend_root=tmp_path,
            )

    assert "Profile" in exc_info.value.unverified
    assert fired == [], (
        "send_email_tracked called Resend even though the UI claim linter "
        "should have hard-stopped first."
    )


def test_ui_claim_lint_passes_clean_copy(monkeypatch, tmp_path):
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")

    app_dir = tmp_path / "src" / "app" / "settings"
    app_dir.mkdir(parents=True)
    (app_dir / "page.tsx").write_text("<h1>Settings</h1>")

    def fake_post(url, **_kwargs):
        if "resend.com" in url:
            return _mock_resp(200, {"id": "rsnd_ok"})
        return _mock_resp(201, {})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        result = send_email_tracked(
            to="x@example.com",
            subject="s",
            html="<p>Open Settings to update your delivery time.</p>",
            user_id="u", profile_person_key="k",
            frontend_root=tmp_path,
        )

    assert result["id"] == "rsnd_ok"
    supabase_creds.reset_cache()


def test_ui_claim_lint_skipped_when_no_frontend_root(monkeypatch):
    """Default behaviour: no frontend_root and no env var -> no lint, send fires."""
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")
    monkeypatch.delenv("EMPIRE_FRONTEND_ROOT", raising=False)

    def fake_post(url, **_kwargs):
        if "resend.com" in url:
            return _mock_resp(200, {"id": "rsnd_unchecked"})
        return _mock_resp(201, {})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        result = send_email_tracked(
            to="x@example.com",
            subject="s",
            html="<p>Update on the Profile page (no lint -> fine).</p>",
            user_id="u", profile_person_key="k",
        )

    assert result["id"] == "rsnd_unchecked"
    supabase_creds.reset_cache()


def test_ui_claim_lint_uses_env_var(monkeypatch, tmp_path):
    """EMPIRE_FRONTEND_ROOT picks up the lint without explicit kwarg."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    app_dir = tmp_path / "src" / "app" / "settings"
    app_dir.mkdir(parents=True)
    (app_dir / "page.tsx").write_text("<h1>Settings</h1>")

    monkeypatch.setenv("EMPIRE_FRONTEND_ROOT", str(tmp_path))

    fired: list[str] = []

    def fake_post(url, **_kwargs):
        fired.append(url)
        return _mock_resp(200, {"id": "x"})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        with pytest.raises(UnverifiedUIClaim):
            send_email_tracked(
                to="x@example.com",
                subject="s",
                html="<p>Open the Profile page</p>",
                user_id="u", profile_person_key="k",
            )

    assert fired == []


# ── copy_guard_context wiring ─────────────────────────────────────────────


def test_copy_guard_blocks_kbk_reel_with_sanganer(monkeypatch):
    """A KBK reel-context send referencing Sanganer must hard-stop pre-Resend."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    fired: list[str] = []

    def fake_post(url, **_kwargs):
        fired.append(url)
        return _mock_resp(200, {"id": "x"})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        with pytest.raises(CopyGuardViolation):
            send_email_tracked(
                to="x@example.com",
                subject="s",
                html="<p>Block-printed in Sanganer for 100 years</p>",
                user_id="u",
                profile_person_key="k",
                copy_guard_context="kbk_reel",
            )

    assert fired == []


def test_copy_guard_blocks_kbk_curtain_natural_dye(monkeypatch):
    """A KBK curtain-context send claiming natural dye must hard-stop."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    fired: list[str] = []

    def fake_post(url, **_kwargs):
        fired.append(url)
        return _mock_resp(200, {"id": "x"})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        with pytest.raises(CopyGuardViolation):
            send_email_tracked(
                to="x@example.com",
                subject="s",
                html="<p>Hand-printed with natural dye from Rajasthan</p>",
                user_id="u",
                profile_person_key="k",
                copy_guard_context="kbk_curtain",
            )

    assert fired == []


def test_copy_guard_skipped_when_context_omitted(monkeypatch):
    """Without copy_guard_context, Sanganer copy still ships (back-compat)."""
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")

    posts: list[str] = []

    def fake_post(url, **_kwargs):
        posts.append(url)
        if "resend.com" in url:
            return _mock_resp(200, {"id": "rsnd_x"})
        return _mock_resp(201, {})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        result = send_email_tracked(
            to="x@example.com",
            subject="s",
            html="<p>Block-printed in Sanganer</p>",
            user_id="u",
            profile_person_key="k",
        )

    assert result["id"] == "rsnd_x"
    supabase_creds.reset_cache()


def test_copy_guard_warn_only_does_not_block(monkeypatch, capsys):
    """AI-writing tells (warn-level) print to stderr but don't stop the send."""
    supabase_creds.reset_cache()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_k")

    posts: list[str] = []

    def fake_post(url, **_kwargs):
        posts.append(url)
        if "resend.com" in url:
            return _mock_resp(200, {"id": "rsnd_x"})
        return _mock_resp(201, {})

    with patch.object(sender.httpx, "post", side_effect=fake_post):
        result = send_email_tracked(
            to="x@example.com",
            subject="s",
            html="<p>This is groundbreaking - a transformative work of insight</p>",
            user_id="u",
            profile_person_key="k",
            copy_guard_context="general",
        )

    assert result["id"] == "rsnd_x"
    captured = capsys.readouterr()
    assert "copy guard warn" in captured.err
    supabase_creds.reset_cache()
