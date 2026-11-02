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

## Run locally with Docker Compose

1. Copy `.env.example` to `.env`, set `POSTGRES_PASSWORD`, and set `GEO_CLOUD_ENV=development` for isolated local use.
2. From the repository root, start the services:

   ```bash
   docker compose --env-file .env -f geospatial/compose.yaml up --build
   ```

3. Open `http://127.0.0.1:18080`. The API is behind Nginx; PostgreSQL and dataset files use persistent named volumes, and Redis provides the dataset metadata cache.

The Compose setup binds HTTP to localhost by default. Do not expose it publicly in development mode. A public deployment needs production authentication and HTTPS termination; use a reverse proxy for public traffic. Set `GEO_CLOUD_HTTP_PORT` if port 18080 is already in use. Use `docker compose --env-file .env -f geospatial/compose.yaml down` to stop services without deleting persisted data.

### Production server with Nginx TLS

The local Compose file uses `geospatial/nginx/local.conf`; the production Nginx server configuration is `geospatial/nginx/default.conf` and is set up for `geo-cloud.duckdns.org`. After pointing that DuckDNS name to your server and obtaining a Let's Encrypt certificate under `/etc/letsencrypt/live/geo-cloud.duckdns.org/`, set `GEO_CLOUD_ENV=production` and the production Auth0 values in `.env`, then run from the repository root:

```bash
docker compose --env-file .env \
  -f geospatial/compose.yaml \
  -f geospatial/compose.production.yaml \
  up --build -d
```

The production override publishes ports 80 and 443 and mounts the host's Let's Encrypt certificates read-only. Configure the server firewall to allow those ports and keep PostgreSQL private. This production configuration requires Docker Compose 2.24 or newer for `!override`.

## Dataset management

Dataset listing, metadata, bounded JSON/multipart uploads, soft deletion, and the storage
adapter are documented in [`docs/dataset-management.md`](docs/dataset-management.md).
Configure `GEO_CLOUD_STORAGE_BACKEND=local` and `GEO_CLOUD_MAX_UPLOAD_BYTES=26214400`.
After applying migrations, run `python -m geospatial.backfill_dataset_metadata` and review
its report before relying on the enriched metadata for existing datasets.
