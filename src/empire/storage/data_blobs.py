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


def _content_obj_path(key: str, sha: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Content-addressed object path. Immutable: a given (key, sha) always maps
    to the same object, so a write never overwrites the bytes a live pointer is
    still referencing. The pointer row is the single source of truth for which
    object is current."""
    return f"{prefix}/{key}/{sha}.csv"


def _current_gs_uri(key: str) -> str:
    """Return the gs_uri the pointer row currently references, or '' if none.
    Used to clean up the superseded object after a pointer flip."""
    url, sb_key = get_supabase_creds()
    try:
        resp = httpx.get(
            f"{url}/rest/v1/data_store",
            headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
            params={"select": "gs_uri", "data_key": f"eq.{key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        rows = resp.json()
        return (rows[0].get("gs_uri") or "") if rows else ""
    except Exception:
        return ""


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

    client = _gcs_client()
    bkt = client.bucket(bucket)

    # 1) Upload to a content-addressed object and verify the bytes landed.
    #    Because the object name embeds the SHA, this write can never clobber
    #    the bytes a live pointer still references — so a failed pointer upsert
    #    (step 2) cannot produce a pointer/blob divergence. We re-download and
    #    re-hash to catch a silent partial/corrupt upload before we advertise
    #    the object in the pointer row.
    obj_path = _content_obj_path(key, sha, prefix)
    gs_uri = f"gs://{bucket}/{obj_path}"
    old_gs_uri = _current_gs_uri(key)
    blob = bkt.blob(obj_path)
    last_err: Exception | None = None
    for _attempt in range(3):
        blob.upload_from_string(csv_text, content_type="text/csv; charset=utf-8")
        try:
            readback = bkt.blob(obj_path).download_as_text()
        except Exception as e:  # transient read failure — retry
            last_err = e
            continue
        if _sha256(readback) == sha:
            break
        last_err = DataBlobChecksumMismatch(
            f"key={key!r}: GCS readback SHA != intended SHA {sha}"
        )
    else:
        raise RuntimeError(f"put_csv: GCS write verification failed for {key!r}: {last_err}")

    # 2) Upsert pointer row in Supabase — atomically flips `key` to the new object.
    url, sb_key = get_supabase_creds()
    row = {
        "data_key": key,
        "gs_uri": gs_uri,
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

    # 3) Best-effort cleanup of the now-superseded object. Only ever deletes the
    #    specific gs_uri the pointer referenced *before* this write, so it can
    #    never delete the object we just published or one a concurrent writer
    #    published. Unchanged content (old==new) is a no-op.
    if old_gs_uri and old_gs_uri != gs_uri:
        try:
            ob, op = _parse_gs_uri(old_gs_uri)
            client.bucket(ob).blob(op).delete()
        except Exception:
            pass

    return {
        "key": key,
        "gs_uri": gs_uri,
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
    # 1) Delete GCS object (best-effort). Prefer the content-addressed object the
    #    pointer references; fall back to the legacy flat path for old rows.
    try:
        client = _gcs_client()
        gs = _current_gs_uri(key)
        if gs:
            ob, op = _parse_gs_uri(gs)
            client.bucket(ob).blob(op).delete()
        else:
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
