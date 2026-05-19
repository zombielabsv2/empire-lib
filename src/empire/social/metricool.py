"""Metricool client — one publishing layer for the whole empire.

Every brand's content pipeline (KBK carousels, AstroMedha zodiac cards,
Iqbal couplets, Moonpath articles, ...) renders its own assets, then calls
``MetricoolClient.schedule_post`` to push a scheduled post into Metricool.
Metricool publishes to Instagram / LinkedIn / Pinterest / Facebook / etc.

Why this exists once, here: building a direct integration per platform means
a Meta / LinkedIn / TikTok developer app and app review for each. Metricool is
already the reviewed partner app, so the empire only ever authenticates to
*Metricool* — a single API token — and never touches a platform dev portal.

Auth (Metricool API, base https://app.metricool.com/api):
  - header  X-Mc-Auth: <userToken>
  - query   userId, blogId   (blogId = the brand)

Credentials come from the environment, never hard-coded:
  - METRICOOL_USER_TOKEN   (the REST API token, Metricool > Settings > API)
  - METRICOOL_USER_ID      (numeric account id)

Media note: Metricool will not publish a post pointing at an arbitrary
external URL. Each media URL must first be passed through
``normalize_media`` — Metricool copies it to its own servers and returns the
URL to use in the post. ``schedule_post`` does this automatically.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from empire.exceptions import EmpireLibError

BASE_URL = "https://app.metricool.com/api"
DEFAULT_TIMEZONE = "Asia/Kolkata"
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# Networks Metricool can fully auto-publish from an API-created post.
# Instagram Stories with link stickers and Reels with trending audio are the
# two exceptions — they are platform (Meta) limits, not Metricool limits, and
# are handled by posting those as drafts for a human to finish.
AUTOPUBLISH_NETWORKS = {
    "instagram", "facebook", "linkedin", "pinterest",
    "tiktok", "youtube", "threads", "bluesky", "gmb", "twitter",
}


class MetricoolError(EmpireLibError):
    """Raised on any Metricool credential or API failure."""


class MetricoolAuthMissing(MetricoolError):
    """METRICOOL_USER_TOKEN / METRICOOL_USER_ID not in the environment."""


def _resolve_creds(user_token: str | None, user_id: str | None) -> tuple[str, str]:
    token = user_token or os.environ.get("METRICOOL_USER_TOKEN", "")
    uid = user_id or os.environ.get("METRICOOL_USER_ID", "")
    if not token or not uid:
        raise MetricoolAuthMissing(
            "Set METRICOOL_USER_TOKEN and METRICOOL_USER_ID in the environment "
            "(token: Metricool > Settings > API). These were not found."
        )
    return token, str(uid)


@dataclass
class ScheduledPostResult:
    """Outcome of a schedule_post call."""

    ok: bool
    post_id: str | None = None
    draft: bool = False
    networks: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class MetricoolClient:
    """Thin, typed wrapper over the Metricool REST API.

    Parameters
    ----------
    user_token, user_id
        Override the environment credentials. Normally left as None so the
        values come from METRICOOL_USER_TOKEN / METRICOOL_USER_ID.
    """

    def __init__(self, user_token: str | None = None, user_id: str | None = None):
        self.user_token, self.user_id = _resolve_creds(user_token, user_id)

    # -- low level -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"X-Mc-Auth": self.user_token, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json: dict | None = None) -> Any:
        params = dict(params or {})
        params.setdefault("userId", self.user_id)
        try:
            resp = httpx.request(
                method, f"{BASE_URL}{path}",
                headers=self._headers(), params=params, json=json,
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:  # network-level
            raise MetricoolError(f"Metricool request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise MetricoolError(
                f"Metricool {method} {path} -> {resp.status_code}: {resp.text[:400]}"
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- brands --------------------------------------------------------------
    def list_brands(self) -> list[dict]:
        """All brands on the account, each with id (blogId), label, timezone."""
        data = self._request("GET", "/v2/settings/brands")
        return data.get("data", []) if isinstance(data, dict) else (data or [])

    def find_brand(self, name: str) -> dict | None:
        """First brand whose label/description contains `name` (case-insensitive)."""
        needle = name.strip().lower()
        for b in self.list_brands():
            hay = " ".join(str(b.get(k, "")) for k in
                            ("label", "description", "title", "ownerUsername")).lower()
            if needle in hay:
                return b
        return None

    # -- media ---------------------------------------------------------------
    def normalize_media(self, public_url: str, blog_id: int | str) -> str:
        """Copy an external media URL onto Metricool's servers; return the new URL.

        Required before the URL can be used in a scheduled post. The source URL
        must be publicly reachable.
        """
        data = self._request(
            "GET", "/actions/normalize/image/url",
            params={"url": public_url, "blogId": blog_id},
        )
        if isinstance(data, dict):
            url = data.get("url") or data.get("data", {}).get("url")
            if url:
                return url
        if isinstance(data, str) and data.startswith("http"):
            return data
        raise MetricoolError(f"normalize_media returned no URL: {data!r}")

    # -- scheduling ----------------------------------------------------------
    def schedule_post(
        self,
        *,
        blog_id: int | str,
        text: str,
        networks: list[str],
        publish_at: datetime,
        media: list[str] | None = None,
        post_type: str = "post",
        draft: bool = False,
        autopublish: bool = True,
        first_comment: str = "",
        timezone: str = DEFAULT_TIMEZONE,
        alt_texts: list[str] | None = None,
        normalize: bool = True,
        pinterest: dict | None = None,
    ) -> ScheduledPostResult:
        """Create a scheduled post in Metricool's planner.

        Parameters
        ----------
        blog_id        target brand (blogId).
        text           caption / post body.
        networks       e.g. ["instagram", "facebook", "pinterest"].
        publish_at     local datetime to publish (interpreted in `timezone`).
        media          list of PUBLIC media URLs; normalized automatically.
        post_type      "post" | "reel" | "story" (per-network format hint).
        draft          True = save as draft (nothing publishes; for human review).
        autopublish    True = Metricool publishes itself; False = sends a phone
                       notification to finish in-app (needed for IG Story links
                       and Reels with trending audio).
        first_comment  optional first comment (hashtags, link).
        pinterest      Pinterest pin metadata (only used if "pinterest" is in
                       `networks`): keys `title` (pin title), `link`
                       (destination URL), `board_id` (existing board) and/or
                       `board_name` (board to create/reuse by name).
        """
        media = list(media or [])
        if normalize and media:
            media = [self.normalize_media(m, blog_id) for m in media]

        body: dict[str, Any] = {
            "publicationDate": {
                "dateTime": publish_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "timezone": timezone,
            },
            "text": text,
            "firstCommentText": first_comment or "",
            "providers": [{"network": n} for n in networks],
            "media": media,
            "mediaAltText": alt_texts or [],
            "autoPublish": bool(autopublish),
            "draft": bool(draft),
            "shortener": False,
            # True = Metricool copies the media onto its own CDN at schedule
            # time. Required: brand pipelines stage media to the transient GCS
            # bucket (rxj-metricool-staging, 2-day lifecycle), so a post
            # scheduled further out than 2 days would otherwise publish a
            # dead image link. With this True the post is self-contained.
            "saveExternalMediaFiles": True,
            "hasNotReadNotes": False,
        }
        body.update(self._network_data(networks, post_type, autopublish,
                                       pinterest=pinterest))

        data = self._request(
            "POST", "/v2/scheduler/posts",
            params={"blogId": blog_id}, json=body,
        )
        post_id = None
        if isinstance(data, dict):
            post_id = str(data.get("id") or data.get("data", {}).get("id") or "") or None
        return ScheduledPostResult(
            ok=True, post_id=post_id, draft=draft, networks=list(networks),
            raw=data if isinstance(data, dict) else {"response": data},
        )

    @staticmethod
    def _network_data(networks: list[str], post_type: str,
                      autopublish: bool,
                      pinterest: dict | None = None) -> dict[str, Any]:
        """Per-network data blocks the scheduler expects alongside `providers`."""
        t = post_type.lower()
        out: dict[str, Any] = {}
        if "instagram" in networks:
            ig = {"autoPublish": autopublish, "type": t.upper()}
            if t == "reel":
                ig["showReelOnFeed"] = True
            out["instagramData"] = ig
        if "facebook" in networks:
            out["facebookData"] = {"type": "REEL" if t == "reel" else "POST"}
        if "tiktok" in networks:
            out["tiktokData"] = {"autoPublish": autopublish}
        if "youtube" in networks:
            out["youtubeData"] = {"type": "SHORT" if t in ("reel", "short") else "VIDEO"}
        if "linkedin" in networks:
            out["linkedinData"] = {"documentTitle": ""}
        if "pinterest" in networks:
            # ScheduledPostPinterestData accepts exactly: boardId, pinTitle,
            # pinLink, pinNewFormat. Metricool's API has no board-listing
            # endpoint, so a pin created without boardId is a draft the human
            # finishes (picks the board) in the Planning tab.
            p = pinterest or {}
            pin: dict[str, Any] = {}
            if p.get("title"):
                pin["pinTitle"] = p["title"]
            if p.get("link"):
                pin["pinLink"] = p["link"]
            if p.get("board_id"):
                pin["boardId"] = p["board_id"]
            out["pinterestData"] = pin
        return out

    def get_post(self, post_id: str | int, blog_id: int | str) -> dict:
        return self._request("GET", f"/v2/scheduler/posts/{post_id}",
                              params={"blogId": blog_id})

    def delete_post(self, post_id: str | int, blog_id: int | str) -> None:
        self._request("DELETE", f"/v2/scheduler/posts/{post_id}",
                      params={"blogId": blog_id})

    def list_posts(self, blog_id: int | str,
                   start: datetime | str, end: datetime | str) -> list[dict]:
        """List scheduled + published posts for a brand in a datetime window.

        `start`/`end` are naive datetimes (or 'YYYY-MM-DDTHH:MM:SS' strings),
        interpreted in the brand's own timezone. Each post dict carries the
        same shape as `get_post`, including `providers[].status` (PENDING /
        PUBLISHED / ERROR) — handy for spotting posts that failed to publish.
        """
        def _fmt(v: datetime | str) -> str:
            return v if isinstance(v, str) else v.strftime("%Y-%m-%dT%H:%M:%S")

        data = self._request(
            "GET", "/v2/scheduler/posts",
            params={"blogId": blog_id, "start": _fmt(start), "end": _fmt(end)},
        )
        if isinstance(data, dict):
            return data.get("data", [])
        return data if isinstance(data, list) else []


__all__ = [
    "MetricoolClient",
    "ScheduledPostResult",
    "MetricoolError",
    "MetricoolAuthMissing",
    "AUTOPUBLISH_NETWORKS",
    "BASE_URL",
    "DEFAULT_TIMEZONE",
]
