import io
import json
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import geospatial.dataset_repository as repository
import geospatial.routes as routes
from geospatial.dataset_api import decode_dataset_cursor, encode_dataset_cursor, InvalidDatasetCursor
from geospatial.dataset_service import (
    DatasetTooLargeError,
    DatasetUploadError,
    DatasetValidationError,
    create_dataset_from_geojson,
    create_dataset_from_upload,
)
from geospatial.main import app
from geospatial.storage.local import LocalDatasetStorage
from geospatial.models import Dataset

client = TestClient(app)


def point_collection():
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "Nairobi point"},
            "geometry": {"type": "Point", "coordinates": [36.8, -1.2]},
        }],
    }


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def use_local_development_api(monkeypatch):
    monkeypatch.setenv("GEO_CLOUD_ENV", "development")
    previous_db = app.dependency_overrides.get(routes.get_db)
    app.dependency_overrides[routes.get_db] = lambda: FakeSession()
    try:
        yield
    finally:
        if previous_db is None:
            app.dependency_overrides.pop(routes.get_db, None)
        else:
            app.dependency_overrides[routes.get_db] = previous_db


def test_multipart_upload_persists_exact_bytes_and_metadata(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(root))
    monkeypatch.setenv("GEO_CLOUD_STORAGE_BACKEND", "local")
    monkeypatch.setenv("GEO_CLOUD_MAX_UPLOAD_BYTES", "4096")
    payload = json.dumps(point_collection(), indent=2).encode("utf-8")
    session = FakeSession()

    record, validation = create_dataset_from_upload(
        session,
        io.BytesIO(payload),
        "auth0|alice",
        name="  Nairobi properties  ",
        original_filename=r"C:\\uploads\\nairobi_properties.geojson",
    )

    stored_path = root / record.storage_key
    assert stored_path.read_bytes() == payload
    assert validation["accepted"] is True
    assert record.owner_id == "auth0|alice"
    assert record.name == "Nairobi properties"
    assert record.original_filename == "nairobi_properties.geojson"
    assert record.format == "geojson"
    assert record.content_type == "application/geo+json"
    assert record.size_bytes == len(payload)
    assert record.sha256 == sha256(payload).hexdigest()
    assert record.feature_count == 1
    assert record.geometry_types == ["Point"]
    assert record.bbox == [36.8, -1.2, 36.8, -1.2]
    assert record.crs == "OGC:CRS84"
    assert session.commits == 1


def test_upload_rejects_over_limit_without_persisting(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(root))
    monkeypatch.setenv("GEO_CLOUD_MAX_UPLOAD_BYTES", "10")
    session = FakeSession()

    with pytest.raises(DatasetTooLargeError):
        create_dataset_from_upload(
            session,
            io.BytesIO(b"{" + b" " * 100),
            "auth0|alice",
        )

    assert session.added == []
    assert not root.exists() or not list(root.iterdir())


def test_upload_rejects_invalid_json_without_persisting(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(root))
    session = FakeSession()

    with pytest.raises(DatasetUploadError):
        create_dataset_from_upload(session, io.BytesIO(b"not JSON"), "auth0|alice")

    assert session.added == []
    assert not root.exists() or not list(root.iterdir())


def test_upload_rejects_invalid_geojson_without_persisting(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(root))
    session = FakeSession()

    with pytest.raises(DatasetValidationError):
        create_dataset_from_upload(session, io.BytesIO(b"[]"), "auth0|alice")

    assert session.added == []
    assert not root.exists() or not list(root.iterdir())


def test_json_dataset_creation_enforces_size_and_records_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(tmp_path / "datasets"))
    monkeypatch.setenv("GEO_CLOUD_MAX_UPLOAD_BYTES", "4096")
    session = FakeSession()
    record, validation, _ = create_dataset_from_geojson(
        session, point_collection(), "auth0|alice", name="point dataset"
    )
    assert validation["accepted"] is True
    assert record.name == "point dataset"
    assert record.size_bytes > 0
    assert len(record.sha256) == 64
    assert record.feature_count == 1


def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalDatasetStorage(tmp_path / "datasets")
    with pytest.raises(ValueError, match="Invalid dataset storage key"):
        storage.put("../outside.json", io.BytesIO(b"no"))
    with pytest.raises(ValueError, match="Invalid dataset storage key"):
        storage.open("/absolute.json")


def test_dataset_cursor_round_trips_and_rejects_malformed_input():
    record = SimpleNamespace(
        id=uuid4(),
        created_at=datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc),
    )
    cursor = encode_dataset_cursor(record)
    assert decode_dataset_cursor(cursor) == (record.created_at, record.id)
    with pytest.raises(InvalidDatasetCursor):
        decode_dataset_cursor("not-a-cursor")


def test_list_route_scopes_query_to_current_owner(monkeypatch):
    owner_id = "auth0|alice"
    record = SimpleNamespace(
        id=uuid4(),
        name="Nairobi",
        original_filename="nairobi.geojson",
        format="geojson",
        content_type="application/geo+json",
        size_bytes=200,
        sha256="a" * 64,
        feature_count=1,
        geometry_types=["Point"],
        bbox=[36.8, -1.2, 36.8, -1.2],
        crs="OGC:CRS84",
        created_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
        deleted_at=None,
    )
    seen = {}
    previous_owner = app.dependency_overrides.get(routes.get_current_owner)
    previous_db = app.dependency_overrides.get(routes.get_db)
    app.dependency_overrides[routes.get_current_owner] = lambda: owner_id
    app.dependency_overrides[routes.get_db] = lambda: FakeSession()
    monkeypatch.setattr(
        routes,
        "list_owned_datasets",
        lambda db, requested_owner, **kwargs: (
            seen.update(owner=requested_owner, kwargs=kwargs) or [record],
            False,
        ),
    )
    try:
        response = client.get("/datasets?limit=20")
    finally:
        if previous_owner is None:
            app.dependency_overrides.pop(routes.get_current_owner, None)
        else:
            app.dependency_overrides[routes.get_current_owner] = previous_owner
        if previous_db is None:
            app.dependency_overrides.pop(routes.get_db, None)
        else:
            app.dependency_overrides[routes.get_db] = previous_db

    assert response.status_code == 200
    assert seen["owner"] == owner_id
    assert seen["kwargs"]["limit"] == 20
    assert response.json()["items"][0]["name"] == "Nairobi"
    assert response.json()["next_cursor"] is None
    assert "storage_key" not in response.json()["items"][0]


def test_dataset_metadata_route_caches_by_owner(monkeypatch):
    dataset_id = uuid4()
    record = SimpleNamespace(
        id=dataset_id,
        name="Nairobi",
        original_filename="nairobi.geojson",
        format="geojson",
        content_type="application/geo+json",
        size_bytes=200,
        sha256="a" * 64,
        feature_count=1,
        geometry_types=["Point"],
        bbox=[36.8, -1.2, 36.8, -1.2],
        crs="OGC:CRS84",
        created_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
        deleted_at=None,
    )

    class FakeRedis:
        def __init__(self):
            self.values = {}
            self.set_calls = []

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ex):
            self.values[key] = value
            self.set_calls.append((key, ex))

        def delete(self, key):
            self.values.pop(key, None)

    cache = FakeRedis()
    database_calls = []
    monkeypatch.setattr(routes, "redis_client", cache)
    monkeypatch.setattr(
        routes,
        "get_owned_dataset",
        lambda db, requested_id, owner: (
            database_calls.append((requested_id, owner)) or record
        ),
    )

    first_response = client.get(f"/datasets/{dataset_id}/metadata")
    second_response = client.get(f"/datasets/{dataset_id}/metadata")

    cache_key = f"dataset:local-development:{dataset_id}:metadata"
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json() == second_response.json()
    assert database_calls == [(dataset_id, "local-development")]
    assert cache.set_calls == [(cache_key, 60)]


def test_multipart_upload_route_accepts_valid_geojson(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(root))
    monkeypatch.setenv("GEO_CLOUD_MAX_UPLOAD_BYTES", "4096")
    content = json.dumps(point_collection(), indent=2).encode("utf-8")
    response = client.post(
        "/datasets/upload",
        files={"file": ("nairobi.geojson", content, "application/geo+json")},
        data={"name": "Nairobi upload"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Nairobi upload"
    assert body["original_filename"] == "nairobi.geojson"
    assert body["size_bytes"] == len(content)
    assert (root / f"{body['id']}.json").read_bytes() == content


def test_list_route_rejects_limit_above_maximum():
    response = client.get("/datasets?limit=101")
    assert response.status_code == 422


def test_delete_route_returns_204_when_storage_cleanup_completes(monkeypatch):
    dataset_id = uuid4()
    record = SimpleNamespace(
        id=dataset_id,
        storage_backend="local",
        storage_key=f"{dataset_id}.json",
    )
    deleted_calls = []
    marker_calls = []
    monkeypatch.setattr(routes, "soft_delete_owned_dataset", lambda *args: (record, True))
    monkeypatch.setattr(routes, "delete_dataset", lambda *args, **kwargs: deleted_calls.append((args, kwargs)))
    monkeypatch.setattr(routes, "mark_dataset_storage_deleted", lambda *args: marker_calls.append(args) or True)

    response = client.delete(f"/datasets/{dataset_id}")

    assert response.status_code == 204
    assert deleted_calls
    assert marker_calls


def test_delete_route_returns_409_if_dataset_is_in_use(monkeypatch):
    dataset_id = uuid4()
    record = SimpleNamespace(id=dataset_id)
    monkeypatch.setattr(routes, "soft_delete_owned_dataset", lambda *args: (record, False))
    response = client.delete(f"/datasets/{dataset_id}")
    assert response.status_code == 409


def test_delete_route_leaves_cleanup_pending_on_storage_error(monkeypatch):
    dataset_id = uuid4()
    record = SimpleNamespace(
        id=dataset_id,
        storage_backend="local",
        storage_key=f"{dataset_id}.json",
    )
    monkeypatch.setattr(routes, "soft_delete_owned_dataset", lambda *args: (record, True))
    monkeypatch.setattr(
        routes, "delete_dataset", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk"))
    )
    response = client.delete(f"/datasets/{dataset_id}")
    assert response.status_code == 202
    assert response.json()["status"] == "deleted_cleanup_pending"


def test_request_body_middleware_rejects_large_body(monkeypatch):
    # The request cap includes the configured upload size plus 1 MiB multipart
    # overhead. The Content-Length check should reject this before route parsing.
    monkeypatch.setenv("GEO_CLOUD_MAX_UPLOAD_BYTES", "1")
    response = client.post("/buffer", content=b"x" * (1024 * 1024 + 100))
    assert response.status_code == 413


def test_request_body_middleware_rejects_large_chunked_body(monkeypatch):
    monkeypatch.setenv("GEO_CLOUD_MAX_UPLOAD_BYTES", "1")
    chunks = [b"x" * 600_000, b"y" * 600_000]
    response = client.post("/buffer", content=iter(chunks))
    assert response.status_code == 413


def test_repository_listing_filters_owner_and_deleted_rows():
    captured = {}

    class ScalarResult:
        def all(self):
            return []

    class Result:
        def scalars(self):
            return ScalarResult()

    class Session:
        def execute(self, statement):
            captured["statement"] = statement
            return Result()

    records, has_more = repository.list_owned_datasets(
        Session(), "auth0|alice", limit=20
    )
    statement = str(captured["statement"])
    assert records == []
    assert has_more is False
    assert "datasets.owner_id" in statement
    assert "datasets.deleted_at IS NULL" in statement
    assert "LIMIT" in statement


def test_soft_delete_updates_tombstone_when_no_active_job_uses_dataset():
    dataset_id = uuid4()
    record = SimpleNamespace(id=dataset_id, deleted_at=None)

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Session:
        def __init__(self):
            self.values = [record, None]
            self.commits = 0
            self.rollbacks = 0

        def execute(self, statement):
            return Result(self.values.pop(0))

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    session = Session()
    deleted_record, deleted = repository.soft_delete_owned_dataset(
        session, dataset_id, "auth0|alice"
    )
    assert deleted is True
    assert deleted_record is record
    assert record.deleted_at is not None
    assert session.commits == 1
    assert session.rollbacks == 0


def test_soft_delete_refuses_dataset_used_by_active_job():
    dataset_id = uuid4()
    record = SimpleNamespace(id=dataset_id, deleted_at=None)

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Session:
        def __init__(self):
            self.values = [record, uuid4()]
            self.commits = 0
            self.rollbacks = 0

        def execute(self, statement):
            return Result(self.values.pop(0))

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    session = Session()
    returned_record, deleted = repository.soft_delete_owned_dataset(
        session, dataset_id, "auth0|alice"
    )
    assert returned_record is record
    assert deleted is False
    assert record.deleted_at is None
    assert session.commits == 0
    assert session.rollbacks == 1


def test_worker_retries_pending_dataset_storage_cleanup(monkeypatch):
    from datetime import datetime, timezone
    import geospatial.worker as worker

    dataset_id = uuid4()
    record = SimpleNamespace(
        id=dataset_id,
        storage_backend="local",
        storage_key=f"{dataset_id}.json",
        deleted_at=datetime.now(timezone.utc),
        storage_deleted_at=None,
    )

    class Result:
        def scalar_one_or_none(self):
            return record

    class Session:
        def execute(self, statement):
            return Result()

    class Transaction:
        def __enter__(self):
            return Session()

        def __exit__(self, exc_type, exc, tb):
            return False

    class SessionFactory:
        def begin(self):
            return Transaction()

    deleted = []
    monkeypatch.setattr(worker, "SessionLocal", SessionFactory())
    monkeypatch.setattr(
        worker,
        "delete_dataset",
        lambda *args, **kwargs: deleted.append((args, kwargs)),
    )

    assert worker.process_pending_dataset_cleanup(limit=1) == 1
    assert len(deleted) == 1
    assert record.storage_deleted_at is not None


def test_soft_delete_is_idempotent_for_the_same_owner():
    dataset_id = uuid4()
    record = SimpleNamespace(
        id=dataset_id,
        deleted_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
    )

    class Result:
        def scalar_one_or_none(self):
            return record

    class Session:
        def execute(self, statement):
            return Result()

    returned_record, deleted = repository.soft_delete_owned_dataset(
        Session(), dataset_id, "auth0|alice"
    )
    assert returned_record is record
    assert deleted is True
