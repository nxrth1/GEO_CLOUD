from uuid import uuid4

from geospatial.job_repository import get_job
from geospatial.models import Job, Dataset


def test_job_lookup_filters_by_both_job_id_and_owner():
    job_id = uuid4()
    owner_id = "auth0|owner-b"
    captured = {}

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        def execute(self, statement):
            captured["statement"] = statement
            return Result()

    assert get_job(Session(), job_id, owner_id) is None
    statement = captured["statement"]
    assert "jobs.owner_id" in str(statement)
    assert job_id in statement.compile().params.values()
    assert owner_id in statement.compile().params.values()


def test_job_dataset_references_enforce_matching_owner_in_schema():
    constraints = {
        constraint.name: constraint
        for constraint in Job.__table__.foreign_key_constraints
    }

    assert set(constraints) == {
        "fk_jobs_source_dataset_owner",
        "fk_jobs_secondary_dataset_owner",
        "fk_jobs_result_dataset_owner",
    }
    for constraint in constraints.values():
        assert "owner_id" in constraint.column_keys
        assert constraint.referred_table is Dataset.__table__
        assert {element.target_fullname for element in constraint.elements} == {
            "datasets.id",
            "datasets.owner_id",
        }
        assert constraint.deferrable is True
        assert constraint.initially == "DEFERRED"
