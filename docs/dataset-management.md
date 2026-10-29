# Dataset management

This implementation adds owner-scoped dataset listing, metadata, bounded uploads,
soft deletion, and a storage adapter while retaining the existing `POST /datasets`
JSON endpoint and UUID-based dataset IDs.

## Dataset API

- `POST /datasets` accepts `{ "geojson": <FeatureCollection>, "name": "optional name" }`.
- `POST /datasets/upload` accepts multipart form data with a `file` field and an optional `name` field. Only GeoJSON/JSON content accepted by the existing validator is stored.
- `GET /datasets?limit=50&cursor=...` returns metadata, never full dataset contents. `limit` is 1–100 and pagination is cursor-based.
- `GET /datasets/{dataset_id}` returns the GeoJSON content as before.
- `GET /datasets/{dataset_id}/metadata` returns metadata only.
- `DELETE /datasets/{dataset_id}` soft-deletes a dataset. It returns `409` when a queued/running job uses the dataset as an input, `404` if the dataset is not in the caller's active collection, `204` when storage cleanup completes, or `202` when physical cleanup must be retried.

All routes use the verified principal's owner ID. Clients do not provide the owner ID. Dataset metadata/list queries exclude soft-deleted records.

## Metadata

The `datasets` table now records `name`, `original_filename`, `format`, `content_type`,
`storage_backend`, `storage_key`, `size_bytes`, `sha256`, `feature_count`,
`geometry_types`, `bbox`, `crs`, `deleted_at`, and `storage_deleted_at` in addition to
ID, owner, and creation timestamp. `size_bytes` and some descriptive fields are nullable
for existing records until the backfill runs.

A missing CRS member in standard RFC 7946 GeoJSON is recorded as `OGC:CRS84`; a declared
legacy `crs.properties.name` is preserved as descriptive metadata. Bounds use
`[min_x, min_y, max_x, max_y]`. The checksum and size describe the exact bytes stored.
For multipart uploads, original bytes and formatting are preserved; JSON endpoint data is
stored as compact UTF-8 JSON.

## Limits and storage

`GEO_CLOUD_MAX_UPLOAD_BYTES` defaults to `26214400` (25 MiB). The app also applies an
ASGI request-body limit with 1 MiB overhead to reject oversized bodies before request
parsing. The upload handler independently counts streamed file bytes. Adjust any reverse
proxy body-size limit to match this setting plus multipart overhead.

`GEO_CLOUD_STORAGE_BACKEND=local` is the supported adapter. `GEO_CLOUD_DATASETS_DIR`
chooses its root. The storage contract is in `geospatial/storage/base.py`; the local
adapter is in `geospatial/storage/local.py`. The API and worker both read object keys from
dataset metadata. To add object storage, implement the same `put`, `open`, `delete`, and
`exists` methods and register that adapter in `geospatial/storage/__init__.py`, then write
a separate copy-and-verify migration for existing files.

Storage keys are generated server-side and never derived from an uploaded filename. The
local adapter rejects path traversal and writes using a temporary file plus atomic replace.

## Migration and metadata backfill

1. Back up PostgreSQL and the configured dataset storage directory.
2. Apply schema migrations: `python -m geospatial.init_db`.
3. Run the metadata backfill: `python -m geospatial.backfill_dataset_metadata`.
4. Review its JSON report. It reports records with missing objects, unreadable/invalid
   stored JSON, and unregistered local `.json` files. The command does not guess owners or
   silently register orphan files; resolve those anomalies deliberately.
5. Run `pytest` and verify dataset listing, upload, read, deletion, and job processing in
   staging before production deployment.

The backfill command updates content-derived metadata for registered objects that can be
read. It returns a non-zero exit status if anomalies are found, even though successfully
read records are still backfilled.

## Deletion and cleanup

Deletion first marks the dataset as deleted in PostgreSQL, keeping the tombstone row so
historical job foreign keys remain valid. It refuses deletion while an active job depends
on the dataset as an input. Physical deletion is idempotent; if storage deletion succeeds
but its database marker update fails, the worker can retry safely. The background worker
processes pending storage cleanup alongside queued geospatial jobs.
