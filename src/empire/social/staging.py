"""Public media staging for social publishing.

Metricool will not publish a post that points at a private URL — it must be
able to fetch the media to copy it onto its own servers. Brand pipelines
render slides/Reels as local files, so this helper uploads a local file to a
dedicated public GCS bucket and returns the public URL to hand to Metricool.

The bucket (``rxj-metricool-staging``) is intentionally public-read: its only
contents are transient copies of media that is about to be posted publicly on
Instagram anyway, and a lifecycle rule deletes every object after 2 days. No
private data ever goes here.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

from empire.exceptions import EmpireLibError

STAGING_BUCKET = "rxj-metricool-staging"


class MediaStagingError(EmpireLibError):
    """Raised when a local media file cannot be staged for publishing."""


def _upload_via_python_client(path: Path, key: str) -> bool:
    """Try the google-cloud-storage client (works where ADC is a service
    account, e.g. Cloud Run). Returns True on success, False to fall back."""
    try:
        import mimetypes

        from google.cloud import storage
    except ImportError:
        return False
    try:
        ct = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        blob = storage.Client().bucket(STAGING_BUCKET).blob(key)
        blob.upload_from_filename(str(path), content_type=ct)
        return True
    except Exception:  # noqa: BLE001 — fall back to gsutil (e.g. stale user ADC)
        return False


def _upload_via_gsutil(path: Path, key: str) -> None:
    """Upload with the gsutil CLI, which uses the gcloud SDK's own auth."""
    gsutil = shutil.which("gsutil") or shutil.which("gsutil.cmd")
    if not gsutil:
        raise MediaStagingError(
            "media upload failed: the google-cloud-storage client could not "
            "authenticate and gsutil is not on PATH. Either run "
            "'gcloud auth application-default login' or install the gcloud SDK."
        )
    proc = subprocess.run(
        [gsutil, "cp", str(path), f"gs://{STAGING_BUCKET}/{key}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaStagingError(
            f"gsutil upload of {path.name} failed: {proc.stderr.strip()[:300]}"
        )


def stage_public(local_path: str | Path, *, prefix: str = "misc") -> str:
    """Upload a local media file to the public staging bucket; return its URL.

    Tries the google-cloud-storage client first (service-account ADC, e.g. on
    Cloud Run) and falls back to the gsutil CLI (developer machines).

    Parameters
    ----------
    local_path
        Path to the local image / video file.
    prefix
        Folder prefix inside the bucket, normally the brand slug
        (e.g. "kbk", "astromedha", "iqbal").

    Returns
    -------
    str
        A public ``https://storage.googleapis.com/...`` URL, reachable by
        Metricool. The object self-deletes after 2 days.
    """
    path = Path(local_path)
    if not path.is_file():
        raise MediaStagingError(f"media file not found: {path}")

    key = f"{prefix.strip('/')}/{int(time.time())}-{uuid.uuid4().hex[:8]}-{path.name}"

    if not _upload_via_python_client(path, key):
        _upload_via_gsutil(path, key)

    return f"https://storage.googleapis.com/{STAGING_BUCKET}/{key}"


def stage_many(paths: list[str | Path], *, prefix: str = "misc") -> list[str]:
    """Stage a list of local files in order; return their public URLs."""
    return [stage_public(p, prefix=prefix) for p in paths]


__all__ = ["stage_public", "stage_many", "MediaStagingError", "STAGING_BUCKET"]
