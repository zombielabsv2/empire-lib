"""GCS-primary CSV blob store with Supabase pointer rows.

put_csv writes the CSV body to GCS and upserts a pointer row in
public.data_store. The legacy csv_text column is left untouched by default
(skip_csv_text=True is the empire-wide default after the 2026-04-27 IO cut);
pass skip_csv_text=False during dual-write transitions if a reader hasn't
been migrated yet.

get_csv reads the pointer row, fetches the GCS blob if gs_uri is populated,
and verifies content_sha. Falls back to the legacy csv_text column on any
GCS read failure so a transient outage can't take down dashboards.

list_meta returns metadata for every key (no body) — suitable for SSR
homepage dashboards that show "last synced X minutes ago".
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx

from empire.config.supabase_creds import get_supabase_creds
from empire.exceptions import (
    DataBlobChecksumMismatch,
    DataBlobNotFound,
)

DEFAULT_BUCKET = "kbk-content"
DEFAULT_PREFIX = "data_store"


def _gs_uri(key: str, bucket: str = DEFAULT_BUCKET, prefix: str = DEFAULT_PREFIX) -> str:
    return f"gs://{bucket}/{prefix}/{key}.csv"


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    """Split gs://bucket/path → (bucket, path)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"not a gs:// URI: {uri!r}")
    rest = uri[len("gs://"):]
    bucket, _, path = rest.partition("/")
    if not bucket or not path:
        raise ValueError(f"malformed gs:// URI: {uri!r}")
    return bucket, path


def _gcs_client() -> Any:
    from google.cloud import storage  # lazy: keeps non-storage callers light

    from empire.storage.gcs_creds import get_credentials

    return storage.Client(credentials=get_credentials())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def put_csv(
    *,
    key: str,
    csv_text: str,
    source: str,
    row_count: int,
    bucket: str = DEFAULT_BUCKET,
    prefix: str = DEFAULT_PREFIX,
    skip_csv_text: bool = True,
) -> dict:
    """Write a CSV to GCS + upsert pointer row in data_store.

    Returns {"key", "gs_uri", "content_sha", "row_count", "size_bytes"}.

    Set skip_csv_text=False during transition if a not-yet-migrated reader
    still depends on the legacy column. Default skips it because that's the
    whole point of the migration: no more 5 MB blob bodies in WAL.
    """
    if not key:
        raise ValueError("key is required")

    csv_bytes = csv_text.encode("utf-8")
    sha = hashlib.sha256(csv_bytes).hexdigest()

    # 1) Upload blob to GCS
    client = _gcs_client()
    blob = client.bucket(bucket).blob(f"{prefix}/{key}.csv")
    blob.upload_from_string(csv_text, content_type="text/csv")

    # 2) Upsert pointer row in Supabase
    url, sb_key = get_supabase_creds()
    row = {
        "data_key": key,
        "gs_uri": _gs_uri(key, bucket, prefix),
        "content_sha": sha,
        "row_count": int(row_count),
        "size_bytes": len(csv_bytes),
        "source": source,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    if not skip_csv_text:
        row["csv_text"] = csv_text
    else:
        # explicit NULL — clears any stale body left over from pre-migration
        row["csv_text"] = None

    resp = httpx.post(
        f"{url}/rest/v1/data_store",
        headers={
            "apikey": sb_key,
            "Authorization": f"Bearer {sb_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=row,
        timeout=30.0,
    )
    resp.raise_for_status()

    return {
        "key": key,
        "gs_uri": row["gs_uri"],
        "content_sha": sha,
        "row_count": int(row_count),
        "size_bytes": len(csv_bytes),
    }


def get_csv(
    key: str,
    *,
    verify_sha: bool = True,
    raise_on_missing: bool = False,
) -> str | None:
    """Read CSV text. GCS first, csv_text fallback.

    Returns None when the key is missing (or raises DataBlobNotFound if
    raise_on_missing=True). When verify_sha=True (default) and the GCS
    body's SHA does not match the pointer row's content_sha, raises
    DataBlobChecksumMismatch — that's a corrupted pair, not a soft miss.
    """
    url, sb_key = get_supabase_creds()
    resp = httpx.get(
        f"{url}/rest/v1/data_store",
        headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
        params={
            "select": "csv_text,gs_uri,content_sha",
            "data_key": f"eq.{key}",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        if raise_on_missing:
            raise DataBlobNotFound(f"no data_store row for key={key!r}")
        return None
    row = rows[0]

    gs_uri = row.get("gs_uri") or ""
    if gs_uri:
        try:
            bucket_name, obj_path = _parse_gs_uri(gs_uri)
            client = _gcs_client()
            text = client.bucket(bucket_name).blob(obj_path).download_as_text()
            if verify_sha:
                expected = row.get("content_sha") or ""
                actual = _sha256(text)
                if expected and actual != expected:
                    raise DataBlobChecksumMismatch(
                        f"key={key!r}: GCS body SHA {actual} != pointer SHA {expected}"
                    )
            return text
        except DataBlobChecksumMismatch:
            raise
        except Exception:
            # Soft fallback to csv_text — keeps dashboards alive through a
            # transient GCS outage. If csv_text is also empty, we return None.
            pass

    csv_text = row.get("csv_text") or None
    if csv_text is None and raise_on_missing:
        raise DataBlobNotFound(f"key={key!r} has no body in GCS or csv_text")
    return csv_text


def list_meta() -> list[dict]:
    """List all data_store keys with metadata (no body). Sorted by synced_at desc."""
    url, sb_key = get_supabase_creds()
    resp = httpx.get(
        f"{url}/rest/v1/data_store",
        headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
        params={
            "select": "data_key,row_count,size_bytes,source,synced_at,gs_uri,content_sha",
            "order": "synced_at.desc",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def delete_blob(
    key: str,
    *,
    bucket: str = DEFAULT_BUCKET,
    prefix: str = DEFAULT_PREFIX,
) -> bool:
    """Delete the GCS blob and the data_store row. Idempotent."""
    # 1) Delete GCS object (best-effort)
    try:
        client = _gcs_client()
        client.bucket(bucket).blob(f"{prefix}/{key}.csv").delete()
    except Exception:
        pass

    # 2) Delete row
    url, sb_key = get_supabase_creds()
    resp = httpx.delete(
        f"{url}/rest/v1/data_store",
        headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
        params={"data_key": f"eq.{key}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    return True
