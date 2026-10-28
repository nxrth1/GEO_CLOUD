from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

import geospatial.worker as worker


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


@contextmanager
def fake_session():
    yield FakeSession()


def test_worker_does_not_read_a_dataset_owned_by_another_user(monkeypatch):
    dataset_id = uuid4()
    monkeypatch.setattr(worker, "SessionLocal", fake_session)
    monkeypatch.setattr(worker, "get_owned_dataset", lambda *args: None)

    def unexpected_load(_dataset_id):
        raise AssertionError("A non-owned dataset must not be read from disk.")

    monkeypatch.setattr(worker, "load_dataset", unexpected_load)

    with pytest.raises(ValueError, match="unavailable to its owner"):
        worker.execute_job(
            {
                "owner_id": "auth0|owner-b",
                "operation": "buffer",
                "source_dataset_id": dataset_id,
                "secondary_dataset_id": None,
                "parameters": {
                    "projected_crs": "EPSG:32637",
                    "distance_in_metres": 10,
                },
            }
        )


def test_worker_rechecks_secondary_dataset_ownership(monkeypatch):
    source_id = uuid4()
    foreign_secondary_id = uuid4()
    owner_id = "auth0|owner-b"
    valid_geojson = {"type": "FeatureCollection", "features": []}
    checked = []

    monkeypatch.setattr(worker, "SessionLocal", fake_session)

    def owned_dataset(_session, dataset_id, requested_owner):
        checked.append((dataset_id, requested_owner))
        if dataset_id == source_id and requested_owner == owner_id:
            return SimpleNamespace(id=source_id)
        return None

    monkeypatch.setattr(worker, "get_owned_dataset", owned_dataset)
    read_ids = []
    monkeypatch.setattr(
        worker,
        "load_dataset",
        lambda dataset_id: read_ids.append(dataset_id) or valid_geojson,
    )

    with pytest.raises(ValueError, match="unavailable to its owner"):
        worker.execute_job(
            {
                "owner_id": owner_id,
                "operation": "clip",
                "source_dataset_id": source_id,
                "secondary_dataset_id": foreign_secondary_id,
                "parameters": {"analysis_crs": "EPSG:32637"},
            }
        )

    assert checked == [
        (source_id, owner_id),
        (foreign_secondary_id, owner_id),
    ]
    assert read_ids == [source_id]
