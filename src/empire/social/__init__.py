"""Social distribution — one publishing layer for the whole empire.

Currently: Metricool (scheduling + auto-publish to Instagram, LinkedIn,
Pinterest, Facebook, TikTok, YouTube, Threads, Bluesky, Google Business).
"""
from __future__ import annotations

from empire.social.metricool import (
    AUTOPUBLISH_NETWORKS,
    MetricoolAuthMissing,
    MetricoolClient,
    MetricoolError,
    ScheduledPostResult,
)
from empire.social.staging import MediaStagingError, stage_many, stage_public

__all__ = [
    "MetricoolClient",
    "ScheduledPostResult",
    "MetricoolError",
    "MetricoolAuthMissing",
    "AUTOPUBLISH_NETWORKS",
    "stage_public",
    "stage_many",
    "MediaStagingError",
]
