# GEO_CLOUD

FastAPI-based geospatial processing API with a PostgreSQL-backed job queue and a separate background worker.

## Quick start

1. Create a PostgreSQL database and install dependencies with `python -m pip install -r requirements.txt`.
2. Configure environment variables using `.env.example` as a reference. The application reads environment variables directly; it does not load `.env` automatically. Production requires Auth0 configuration.
3. Apply database migrations with `python -m geospatial.init_db`.
4. Start the API and worker in separate terminal sessions:

   ```bash
   uvicorn geospatial.main:app --host 127.0.0.1 --port 8000
   python -m geospatial.worker
   ```

5. Run the tests with `pytest`.

Read [Authentication and resource ownership](docs/ownership-and-authentication.md) before exposing the service beyond isolated local development. In particular, `GEO_CLOUD_ENV=development` shares one development owner across all local requests and must not be used for a network-accessible deployment.

## Dataset management

Dataset listing, metadata, bounded JSON/multipart uploads, soft deletion, and the storage
adapter are documented in [`docs/dataset-management.md`](docs/dataset-management.md).
Configure `GEO_CLOUD_STORAGE_BACKEND=local` and `GEO_CLOUD_MAX_UPLOAD_BYTES=26214400`.
After applying migrations, run `python -m geospatial.backfill_dataset_metadata` and review
its report before relying on the enriched metadata for existing datasets.
