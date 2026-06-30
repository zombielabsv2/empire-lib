"""Tests for empire.github_guard.

The guard must (1) allow filing into a PRIVATE repo, (2) hard-block PUBLIC /
INTERNAL repos with IssueRepoNotPrivate, and (3) fail CLOSED when visibility
can't be determined (missing gh, gh error, unparseable output). All gh calls
are mocked — no live GitHub access.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from empire.exceptions import EmpireLibError, IssueRepoNotPrivate
from empire import github_guard


def _fake_run_factory(visibility=None, *, create_url="https://github.com/o/r/issues/1",
                      view_rc=0, create_rc=0, view_stdout=None):
    """Build a subprocess.run replacement that answers `gh repo view` and
    `gh issue create` based on the args it receives."""
    def fake_run(args, capture_output=True, text=True, timeout=None):
        sub = args[1]  # args == ["gh", "<sub>", ...]
        if sub == "repo":
            out = view_stdout if view_stdout is not None else json.dumps(
                {"visibility": visibility}
            )
            return SimpleNamespace(returncode=view_rc, stdout=out, stderr="boom")
        if sub == "issue":
            return SimpleNamespace(returncode=create_rc, stdout=create_url, stderr="boom")
        raise AssertionError(f"unexpected gh subcommand: {args}")
    return fake_run


@pytest.fixture(autouse=True)
def _gh_on_path(monkeypatch):
    """Pretend gh is installed unless a test overrides it."""
    monkeypatch.setattr(github_guard.shutil, "which", lambda _: "/usr/bin/gh")


def test_private_repo_allows_file_issue(monkeypatch):
    monkeypatch.setattr(
        github_guard.subprocess, "run", _fake_run_factory(visibility="PRIVATE")
    )
    url = github_guard.file_issue("o/r", "Bug: secret", "internal detail", labels=["bug"])
    assert url == "https://github.com/o/r/issues/1"


def test_public_repo_blocks(monkeypatch):
    monkeypatch.setattr(
        github_guard.subprocess, "run", _fake_run_factory(visibility="PUBLIC")
    )
    with pytest.raises(IssueRepoNotPrivate) as exc:
        github_guard.file_issue("o/r", "Bug", "body")
    assert exc.value.visibility == "PUBLIC"
    assert exc.value.repo == "o/r"


def test_internal_repo_blocks(monkeypatch):
    monkeypatch.setattr(
        github_guard.subprocess, "run", _fake_run_factory(visibility="INTERNAL")
    )
    with pytest.raises(IssueRepoNotPrivate):
        github_guard.assert_private_repo("o/r")


def test_visibility_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        github_guard.subprocess, "run", _fake_run_factory(visibility="private")
    )
    assert github_guard.repo_visibility("o/r") == "PRIVATE"
    github_guard.assert_private_repo("o/r")  # must not raise


def test_missing_gh_fails_closed(monkeypatch):
    monkeypatch.setattr(github_guard.shutil, "which", lambda _: None)
    with pytest.raises(EmpireLibError):
        github_guard.assert_private_repo("o/r")


def test_gh_error_fails_closed(monkeypatch):
    monkeypatch.setattr(
        github_guard.subprocess, "run", _fake_run_factory(visibility=None, view_rc=1)
    )
    with pytest.raises(EmpireLibError):
        github_guard.assert_private_repo("o/r")


def test_unparseable_visibility_fails_closed(monkeypatch):
    monkeypatch.setattr(
        github_guard.subprocess, "run",
        _fake_run_factory(view_stdout="not json at all"),
    )
    with pytest.raises(EmpireLibError):
        github_guard.repo_visibility("o/r")


def test_empty_visibility_fails_closed(monkeypatch):
    monkeypatch.setattr(
        github_guard.subprocess, "run", _fake_run_factory(view_stdout="{}")
    )
    with pytest.raises(EmpireLibError):
        github_guard.repo_visibility("o/r")
