from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

import geospatial.dataset_repository as dataset_repository
from geospatial.models import Dataset


def test_saving_dataset_persists_owner_metadata(tmp_path, monkeypatch):
    directory = tmp_path / "datasets"
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(directory))
    added = []

    class Session:
        def add(self, instance):
            added.append(instance)

        def commit(self):
            pass

    owner_id = "auth0|user-123"
    dataset_id = dataset_repository.save_owned_dataset(
        Session(),
        {"type": "FeatureCollection", "features": []},
        owner_id,
    )

    assert (directory / f"{dataset_id}.json").exists()
    assert len(added) == 1
    assert isinstance(added[0], Dataset)
    assert added[0].id == dataset_id
    assert added[0].owner_id == owner_id


def test_database_failure_removes_saved_dataset_file(tmp_path, monkeypatch):
    directory = tmp_path / "datasets"
    monkeypatch.setenv("GEO_CLOUD_DATASETS_DIR", str(directory))
    added = []

    class Session:
        def add(self, instance):
            added.append(instance)

        def commit(self):
            raise SQLAlchemyError("database unavailable")

        def rollback(self):
            pass

    with pytest.raises(SQLAlchemyError, match="database unavailable"):
        dataset_repository.save_owned_dataset(
            Session(),
            {"type": "FeatureCollection", "features": []},
            "auth0|user-123",
        )

    assert len(added) == 1
    assert not (directory / f"{added[0].id}.json").exists()


def test_owned_dataset_query_filters_owner_id():
    dataset_id = uuid4()
    owner_id = "auth0|user-123"
    statement_holder = {}

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        def execute(self, statement):
            statement_holder["statement"] = statement
            return Result()

    assert dataset_repository.get_owned_dataset(
        Session(), dataset_id, owner_id
    ) is None

    statement = statement_holder["statement"]
    assert "datasets.owner_id" in str(statement)
    assert owner_id in statement.compile().params.values()
