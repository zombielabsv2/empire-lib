"""empire.storage — GCS-primary CSV blob storage with Supabase pointer rows.

Architecture (feedback_storage_doctrine.md):
- Body lives in GCS at gs://kbk-content/data_store/{key}.csv
- Pointer row in public.data_store carries gs_uri, content_sha, row_count,
  size_bytes, source, synced_at. Legacy csv_text column is honored as a
  read-side fallback for transitional rows that haven't been migrated yet.

Why: Postgres WAL fills the Supabase Disk IO budget when CSV blobs
(Shopify orders, website traffic) get delete-then-inserted daily. Moving
the blob out of WAL drops IO load roughly in half on the empire shared
project (2026-04-27 diagnosis).

Auth: GCS credentials resolve from GCP_SA_KEY env (raw JSON), then
GCP_SA_KEY in st.secrets, then Application Default Credentials (used by
Cloud Run jobs whose compute SA already has objectAdmin on the bucket).
"""
from __future__ import annotations

from empire.storage.data_blobs import (
    DEFAULT_BUCKET,
    DEFAULT_PREFIX,
    delete_blob,
    get_csv,
    list_meta,
    put_csv,
)

__all__ = [
    "DEFAULT_BUCKET",
    "DEFAULT_PREFIX",
    "delete_blob",
    "get_csv",
    "list_meta",
    "put_csv",
]
