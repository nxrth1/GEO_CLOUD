from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape

import geospatial.routes as routes
from geospatial.main import app
from geospatial.models import JobOperation, JobStatus


client = TestClient(app)


@pytest.fixture(autouse=True)
def local_development_identity(monkeypatch):
    monkeypatch.setenv("GEO_CLOUD_ENV", "development")


@pytest.fixture
def job_db_override():
    class FakeSession:
        def commit(self):
            pass

        def refresh(self, job):
            pass

        def close(self):
            pass

    session = FakeSession()
    previous_override = app.dependency_overrides.get(routes.get_db)
    app.dependency_overrides[routes.get_db] = lambda: session
    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(routes.get_db, None)
        else:
            app.dependency_overrides[routes.get_db] = previous_override


def collection(geometry):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"label": "sample"},
            "geometry": geometry,
        }],
    }


def test_buffer_route_validates_then_returns_buffer_result():
    response = client.post(
        "/buffer",
        json={
            "geojson": collection({
                "type": "Point",
                "coordinates": [0, 0],
            }),
            "projected_crs": "EPSG:32631",
            "distance_in_metres": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["validation"]["accepted"] is True
    assert len(body["result"]["features"]) == 1
    geometry = shape(body["result"]["features"][0]["geometry"])
    assert geometry.geom_type == "Polygon"
    assert body["result"]["features"][0]["properties"] == {"label": "sample"}


def test_buffer_route_rejects_invalid_geojson_before_running_operation(monkeypatch):
    def unexpected_buffer(*args, **kwargs):
        raise AssertionError("Buffer must not run for rejected GeoJSON.")

    monkeypatch.setattr("geospatial.routes.buffer", unexpected_buffer)
    response = client.post(
        "/buffer",
        json={
            "geojson": collection({
                "type": "Point",
                "coordinates": [0, 95],
            }),
            "projected_crs": "EPSG:32631",
            "distance_in_metres": 100,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["validation"]["accepted"] is False


def test_buffer_route_returns_client_error_for_invalid_crs():
    response = client.post(
        "/buffer",
        json={
            "geojson": collection({
                "type": "Point",
                "coordinates": [0, 0],
            }),
            "projected_crs": "not-a-crs",
            "distance_in_metres": 100,
        },
    )

    assert response.status_code == 422


def test_job_creation_rejects_missing_operation_parameters_before_loading_data(
    monkeypatch, job_db_override
):
    def unexpected_load(*args, **kwargs):
        raise AssertionError("Datasets should not be loaded for invalid parameters.")

    monkeypatch.setattr(routes, "load_dataset", unexpected_load)
    response = client.post(
        "/jobs",
        json={
            "operation": "buffer",
            "source_dataset_id": str(uuid4()),
            "parameters": {"projected_crs": "EPSG:32631"},
        },
    )

    assert response.status_code == 422
    assert "distance_in_metres" in response.json()["detail"]["errors"][0]


def test_job_creation_requires_secondary_dataset(monkeypatch, job_db_override):
    def unexpected_load(*args, **kwargs):
        raise AssertionError("Datasets should not be loaded when the mask is missing.")

    monkeypatch.setattr(routes, "load_dataset", unexpected_load)
    response = client.post(
        "/jobs",
        json={
            "operation": "clip",
            "source_dataset_id": str(uuid4()),
            "parameters": {"analysis_crs": "EPSG:32631"},
        },
    )

    assert response.status_code == 422
    assert "secondary_dataset_id" in response.json()["detail"]["errors"][0]


def test_job_creation_cannot_use_another_owners_dataset(monkeypatch, job_db_override):
    def unexpected_load(*args, **kwargs):
        raise AssertionError("A foreign dataset must not be read.")

    monkeypatch.setattr(routes, "get_owned_datasets_for_update", lambda *args: {})
    monkeypatch.setattr(routes, "load_dataset", unexpected_load)
    response = client.post(
        "/jobs",
        json={
            "operation": "buffer",
            "source_dataset_id": str(uuid4()),
            "parameters": {
                "projected_crs": "EPSG:32631",
                "distance_in_metres": 100,
            },
        },
    )

    assert response.status_code == 404


def test_job_creation_accepts_valid_request_and_persists_queued_job(
    monkeypatch, job_db_override
):
    job_record = SimpleNamespace(
        id=uuid4(),
        operation=JobOperation.BUFFER,
        status=JobStatus.QUEUED,
    )
    saved_requests = []
    valid_geojson = collection({"type": "Point", "coordinates": [0, 0]})

    monkeypatch.setattr(routes, "load_dataset", lambda dataset_id: valid_geojson)
    monkeypatch.setattr(
        routes,
        "get_owned_datasets_for_update",
        lambda db, dataset_ids, owner_id: {
            dataset_id: SimpleNamespace(id=dataset_id) for dataset_id in dataset_ids
        },
    )
    monkeypatch.setattr(
        routes,
        "create_queued_job",
        lambda *args, **kwargs: saved_requests.append(kwargs) or job_record,
    )
    response = client.post(
        "/jobs",
        json={
            "operation": "buffer",
            "source_dataset_id": str(uuid4()),
            "parameters": {
                "projected_crs": "EPSG:32631",
                "distance_in_metres": 100,
            },
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert len(saved_requests) == 1
    assert saved_requests[0]["owner_id"] == routes.get_current_owner()


def test_dataset_cannot_be_read_without_matching_owner(
    monkeypatch, job_db_override
):
    def unexpected_load(*args, **kwargs):
        raise AssertionError("A foreign dataset must not be read from storage.")

    monkeypatch.setattr(routes, "get_owned_dataset", lambda *args: None)
    monkeypatch.setattr(routes, "load_dataset", unexpected_load)
    response = client.get(f"/datasets/{uuid4()}")

    assert response.status_code == 404


def test_dataset_creation_saves_the_current_owner(monkeypatch, job_db_override):
    saved_owners = []
    dataset_id = uuid4()
    valid_geojson = collection({"type": "Point", "coordinates": [0, 0]})
    now = "2026-10-29T09:00:00+00:00"
    record = SimpleNamespace(
        id=dataset_id,
        name="Nairobi points",
        original_filename=None,
        format="geojson",
        content_type="application/geo+json",
        size_bytes=100,
        sha256="a" * 64,
        feature_count=1,
        geometry_types=["Point"],
        bbox=[0, 0, 0, 0],
        crs="OGC:CRS84",
        created_at=__import__("datetime").datetime.fromisoformat(now),
        deleted_at=None,
    )
    monkeypatch.setattr(
        routes,
        "create_dataset_from_geojson",
        lambda db, geojson, owner_id, name=None: (
            saved_owners.append(owner_id) or record,
            {"accepted": True},
            100,
        ),
    )
    response = client.post(
        "/datasets",
        json={"geojson": valid_geojson, "name": "Nairobi points"},
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(dataset_id)
    assert response.json()["name"] == "Nairobi points"
    assert saved_owners == [routes.get_current_owner()]


def test_job_status_is_scoped_to_the_current_owner(monkeypatch, job_db_override):
    monkeypatch.setattr(routes, "get_job", lambda *args: None)
    response = client.get(f"/jobs/{uuid4()}")

    assert response.status_code == 404


def test_production_dataset_endpoint_requires_a_bearer_token(
    monkeypatch, job_db_override
):
    monkeypatch.setenv("GEO_CLOUD_ENV", "production")
    monkeypatch.setenv("AUTH0_DOMAIN", "example.us.auth0.com")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://geo-cloud.example/api")
    response = client.get(f"/datasets/{uuid4()}")

    assert response.status_code == 401


def test_job_creation_rejects_foreign_secondary_dataset(monkeypatch, job_db_override):
    source_id = uuid4()
    foreign_mask_id = uuid4()
    owner_id = "auth0|owner-b"
    dataset_owners = {source_id: owner_id, foreign_mask_id: "auth0|owner-a"}
    valid_geojson = collection({"type": "Point", "coordinates": [0, 0]})
    read_ids = []

    previous_owner_override = app.dependency_overrides.get(routes.get_current_owner)
    app.dependency_overrides[routes.get_current_owner] = lambda: owner_id
    try:
        monkeypatch.setattr(
            routes,
            "get_owned_datasets_for_update",
            lambda db, dataset_ids, requested_owner: {
                dataset_id: SimpleNamespace(id=dataset_id)
                for dataset_id in dataset_ids
                if dataset_owners.get(dataset_id) == requested_owner
            },
        )
        monkeypatch.setattr(
            routes,
            "load_dataset",
            lambda dataset_id: read_ids.append(dataset_id) or valid_geojson,
        )
        response = client.post(
            "/jobs",
            json={
                "operation": "clip",
                "source_dataset_id": str(source_id),
                "secondary_dataset_id": str(foreign_mask_id),
                "parameters": {"analysis_crs": "EPSG:32637"},
            },
        )
    finally:
        if previous_owner_override is None:
            app.dependency_overrides.pop(routes.get_current_owner, None)
        else:
            app.dependency_overrides[routes.get_current_owner] = previous_owner_override

    assert response.status_code == 404
    assert read_ids == [source_id]


def test_dataset_access_is_scoped_to_authenticated_owner(monkeypatch, job_db_override):
    owner_id = "auth0|owner-b"
    foreign_dataset_id = uuid4()

    previous_owner_override = app.dependency_overrides.get(routes.get_current_owner)
    app.dependency_overrides[routes.get_current_owner] = lambda: owner_id
    try:
        monkeypatch.setattr(routes, "get_owned_dataset", lambda *args: None)
        monkeypatch.setattr(
            routes,
            "load_dataset",
            lambda *_: (_ for _ in ()).throw(
                AssertionError("Foreign dataset files must not be read.")
            ),
        )
        response = client.get(f"/datasets/{foreign_dataset_id}")
    finally:
        if previous_owner_override is None:
            app.dependency_overrides.pop(routes.get_current_owner, None)
        else:
            app.dependency_overrides[routes.get_current_owner] = previous_owner_override

    assert response.status_code == 404
