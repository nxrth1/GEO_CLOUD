# Authentication and resource ownership

## Current security model

This application uses **user-level ownership**. In production, the API validates an Auth0 access token and uses its verified `sub` claim as the stable `owner_id`. A client-supplied `owner_id` is never accepted. Organization/team sharing is not implemented; if that becomes a product requirement, use a verified organization/tenant claim as the authorization boundary and retain `created_by` separately for audit history.

The `/datasets` and `/jobs` routes, including dataset-backed spatial-operation routes, require a trusted principal. Repository lookups scope dataset and job IDs to `owner_id`; inaccessible records return `404` so the API does not confirm that another user's ID exists. The stateless `/buffer`, `/clip`, `/difference`, `/dissolve`, `/spatial_join`, and `/nearest_feature` routes operate only on GeoJSON sent in the current request and do not read persisted datasets or jobs.

The worker re-checks that each persisted input dataset belongs to the owner recorded on its job. It creates result dataset metadata using the job's persisted owner, not a user-supplied value or a bearer token. The worker therefore needs database access but does not need a user's access token.

## Configure Auth0

1. Create an Auth0 API and set its Identifier to the value used for `AUTH0_AUDIENCE`.
2. Configure the API to issue RS256-signed access tokens.
3. Set `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, and `AUTH0_ISSUER` in the API and worker environment. `AUTH0_ISSUER` should be the exact issuer value in the access token, normally `https://<your-tenant-domain>/`.
4. Set `GEO_CLOUD_ENV=production`. Production mode fails closed: missing Auth0 configuration returns `503`; absent, invalid, expired, wrong-issuer, or wrong-audience bearer tokens are rejected.
5. Use `GEO_CLOUD_ENV=development` only for isolated local development. That mode intentionally maps all requests to one shared `local-development` owner and is **not** multi-user authentication.

The frontend must request an **access token for this API** (using the API audience), then send it on persisted-resource calls as `Authorization: Bearer <access-token>`. Do not send an Auth0 ID token in place of an API access token, and do not send an `owner_id` field: the server derives ownership from the validated token's `sub` claim.

Copy `.env.example` to `.env` for reference, then configure environment variables through your secret manager or process manager. The application does not automatically load `.env` files. Never commit real credentials.

## Apply the schema migration

Back up the database first, then set `DATABASE_URL` and run:

```bash
python -m geospatial.init_db
```

This applies Alembic migrations through `0004_enforce_job_dataset_ownership`. New dataset rows have a non-null `owner_id`; new jobs persist their owner. Composite foreign keys enforce at the database layer that each job's source, optional secondary, and optional result dataset belong to the same owner as the job. Deploy the API and worker from the same code version after the migration.

### Existing data and the legacy sentinel

Migration `0003_resource_ownership` deliberately assigns existing jobs and dataset IDs referenced by those jobs to `__legacy_unowned__`. That sentinel is not a real user, so ordinary authenticated users cannot access those old jobs/datasets. This is intentional: existing records must not become public or be guessed to belong to the first user who accesses them.

Old GeoJSON files on disk that are **not referenced by an existing job** cannot be safely assigned an owner from the current data model. They receive no metadata row and remain inaccessible through the API. Before production rollout, inventory those files and either delete them or explicitly map them to verified users using a reviewed one-time migration. Do not bulk-assign them to an arbitrary user. Existing sentinel-owned data can likewise be reassigned only after its rightful owner is established and recorded in an audited transaction that updates the relevant dataset and job owner IDs together. The ownership foreign keys are deferred until transaction commit to allow this controlled atomic reassignment; all references must agree at commit.

## Run and test

Install the dependencies from `requirements.txt`, set the environment variables, and start the API and worker in separate processes:

```bash
uvicorn geospatial.main:app --host 127.0.0.1 --port 8000
python -m geospatial.worker
```

Run tests with:

```bash
pytest
```

Tests in local development mode exercise application logic. The authentication unit tests generate signed test tokens and verify issuer, audience, expiry, and subject validation. Before deployment, also test the real Auth0 access-token flow in a staging environment.

## Operational notes

- Keep `DATABASE_URL`, Auth0 settings, and storage directory consistent across API and worker processes.
- Use persistent, access-controlled storage for `GEO_CLOUD_DATASETS_DIR`; the current file store is local filesystem storage, not object storage.
- Keep production CORS origins restricted to known frontend origins. CORS is browser policy, not authentication.
- `404` is used for unauthorized resource IDs to avoid leaking resource existence.
- Individual user ownership is appropriate for the current model. A future B2B organization model should add a tenant membership/role model rather than trust a tenant ID sent by the client.
