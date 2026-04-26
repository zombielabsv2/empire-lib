"""Autouse pytest fixture blocking live API calls in tests.

Empire rule (feedback_mock_at_api_not_caller.md): tests must mock at the
API boundary, not at the caller. If a helper-lib import path is patched at
the caller level, sibling helpers calling httpx.post directly will still
hit the live API and rack up real cost (the 2026-04-24 leak: $2.32/642
calls because tests patched `caller.httpx.post` instead of the API URL).

This fixture patches httpx.post, httpx.AsyncClient.post, and requests.post
to raise LiveAPIBlocked if the URL host matches any banned domain.

Activation: any project's conftest.py adds:

    from empire.test.guards import block_live_api_hosts  # noqa: F401

The fixture is autouse, scope=session, so it covers every test in the run.
"""
from __future__ import annotations

from urllib.parse import urlparse

import pytest

from empire.exceptions import LiveAPIBlocked

BANNED_HOSTS: set[str] = {
    "api.anthropic.com",
    "api.resend.com",
    "graph.facebook.com",
    "googleads.googleapis.com",
}


def _check_url(url: str) -> None:
    """Raise LiveAPIBlocked if `url`'s host is in BANNED_HOSTS."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    host = host.lower()
    if host in BANNED_HOSTS:
        raise LiveAPIBlocked(host=host, url=url)


@pytest.fixture(autouse=True, scope="session")
def block_live_api_hosts():
    """Patch httpx + requests post methods to block live API calls in tests.

    Yields control to the test session; restores originals on teardown.
    """
    import httpx

    orig_httpx_post = httpx.post
    orig_async_post = httpx.AsyncClient.post

    def guarded_httpx_post(url, *args, **kwargs):
        _check_url(str(url))
        return orig_httpx_post(url, *args, **kwargs)

    async def guarded_async_post(self, url, *args, **kwargs):
        _check_url(str(url))
        return await orig_async_post(self, url, *args, **kwargs)

    httpx.post = guarded_httpx_post  # type: ignore[assignment]
    httpx.AsyncClient.post = guarded_async_post  # type: ignore[assignment]

    # requests is optional — only patch if installed.
    requests_mod = None
    orig_requests_post = None
    try:
        import requests as requests_mod  # type: ignore[import-not-found]
        orig_requests_post = requests_mod.post

        def guarded_requests_post(url, *args, **kwargs):
            _check_url(str(url))
            return orig_requests_post(url, *args, **kwargs)

        requests_mod.post = guarded_requests_post  # type: ignore[assignment]
    except Exception:
        requests_mod = None

    try:
        yield
    finally:
        httpx.post = orig_httpx_post  # type: ignore[assignment]
        httpx.AsyncClient.post = orig_async_post  # type: ignore[assignment]
        if requests_mod is not None and orig_requests_post is not None:
            requests_mod.post = orig_requests_post  # type: ignore[assignment]
