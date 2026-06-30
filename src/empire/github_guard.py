"""Hard-block guard for auto-filing GitHub issues.

Memory rules don't bind autopilots; code guards do. The 2026-07-01 incident:
the cost-report anomaly engine auto-filed "Bug:" issues — embedding Rahul's
name, internal script names, Supabase/Cloud Run topology, and gcloud
remediation commands — into a repo that was PUBLIC at the time. A scraper
Q&A blog copied one verbatim, Google indexed it, and it surfaced in a vanity
search months later, long after the repo had been made private. Closed/private
issues are still public exposure if the repo was ever public when they were
crawled. See incident_empire_dashboard_public_issue_leak.md.

This module is the sanctioned path for any agent/cron that files issues:

    from empire.github_guard import file_issue
    url = file_issue("zombielabsv2/empire-dashboard", title, body, labels=["bug"])

`file_issue` refuses to write unless the target repo is verified PRIVATE, and
fails CLOSED (raises) when visibility can't be determined — a missing `gh`,
an auth error, or an unparseable response all block the write rather than
risk leaking into a public tracker. Use `assert_private_repo` directly if you
file via your own mechanism (REST, Actions) and just want the gate.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from empire.exceptions import EmpireLibError, IssueRepoNotPrivate

__all__ = ["repo_visibility", "assert_private_repo", "file_issue"]

_GH_TIMEOUT = 30  # seconds — gh repo view / issue create are quick API calls


def _gh(args: list[str]) -> str:
    """Run a `gh` subcommand and return stdout. Fail CLOSED on any error.

    A missing gh binary, a non-zero exit, or a timeout all raise EmpireLibError
    so the caller blocks rather than proceeding without a visibility check.
    """
    if shutil.which("gh") is None:
        raise EmpireLibError(
            "gh CLI not found on PATH — cannot verify repo visibility before "
            "filing an issue. Install GitHub CLI or file via a path that can "
            "confirm the repo is private."
        )
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - timing dependent
        raise EmpireLibError(f"gh {' '.join(args)} timed out after {_GH_TIMEOUT}s") from exc
    if proc.returncode != 0:
        raise EmpireLibError(
            f"gh {' '.join(args)} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return proc.stdout


def repo_visibility(repo: str) -> str:
    """Return the GitHub visibility of `repo` ("PRIVATE" / "PUBLIC" / "INTERNAL").

    `repo` is "owner/name". Raises EmpireLibError if visibility can't be read
    (fail closed). The value is upper-cased so callers can compare against
    "PRIVATE" regardless of how gh casts it across versions.
    """
    out = _gh(["repo", "view", repo, "--json", "visibility"])
    try:
        vis = json.loads(out).get("visibility")
    except (json.JSONDecodeError, AttributeError) as exc:
        raise EmpireLibError(
            f"could not parse visibility for {repo} from gh output: {out!r}"
        ) from exc
    if not vis:
        raise EmpireLibError(f"gh returned no visibility field for {repo}: {out!r}")
    return str(vis).upper()


def assert_private_repo(repo: str) -> None:
    """Raise IssueRepoNotPrivate unless `repo` is verified PRIVATE.

    Call this before filing any issue/PR that contains infra internals or
    personal names. Fails closed: an undeterminable visibility raises
    EmpireLibError (from repo_visibility), never silently passes.
    """
    vis = repo_visibility(repo)
    if vis != "PRIVATE":
        raise IssueRepoNotPrivate(repo=repo, visibility=vis)


def file_issue(
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> str:
    """Sanctioned way to auto-file a GitHub issue. Returns the new issue URL.

    Hard-blocks the write unless `repo` is PRIVATE (see assert_private_repo).
    This is the only path empire crons/agents should use to open issues, so a
    future "make the dashboard public" can't silently re-expose the tracker.
    """
    assert_private_repo(repo)
    args = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
    for label in labels or []:
        args += ["--label", label]
    return _gh(args).strip()
