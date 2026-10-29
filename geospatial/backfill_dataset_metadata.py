"""Backfill metadata for existing rows and report orphaned/missing local files.

Run after applying Alembic migrations:
    python -m geospatial.backfill_dataset_metadata

The command does not invent owner IDs or automatically register unreferenced files.
It updates metadata only for dataset rows whose stored object parses as JSON.
"""
import json
import sys

from sqlalchemy import select

from .database import SessionLocal
from .dataset_metadata import calculate_geojson_metadata
from .datasets import load_dataset_bytes
from .processes import validate_json
from .models import Dataset
from .storage import LocalDatasetStorage


def backfill() -> dict:
    report = {
        "updated": 0,
        "missing_objects": [],
        "invalid_objects": [],
        "unregistered_local_files": [],
    }
    with SessionLocal.begin() as session:
        records = list(session.execute(select(Dataset)).scalars().all())
        registered_local_keys = {
            record.storage_key for record in records if record.storage_backend == "local"
        }
        for record in records:
            try:
                content = load_dataset_bytes(
                    record.id,
                    storage_backend=record.storage_backend,
                    storage_key=record.storage_key,
                )
            except FileNotFoundError:
                report["missing_objects"].append(str(record.id))
                continue
            except Exception as error:
                report["invalid_objects"].append({"id": str(record.id), "error": str(error)})
                continue

            try:
                geojson = json.loads(content.decode("utf-8"))
                if not isinstance(geojson, dict):
                    raise ValueError("GeoJSON root must be an object")
                validation = validate_json(geojson)
                if not validation["accepted"]:
                    raise ValueError("Stored GeoJSON failed critical validation rules")
                metadata = calculate_geojson_metadata(geojson, content=content)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                report["invalid_objects"].append({"id": str(record.id), "error": str(error)})
                continue

            for field, value in metadata.items():
                setattr(record, field, value)
            report["updated"] += 1

    local_storage = LocalDatasetStorage()
    if local_storage.root.exists():
        report["unregistered_local_files"] = sorted(
            path.name
            for path in local_storage.root.glob("*.json")
            if path.name not in registered_local_keys and path.is_file()
        )
    return report


def main() -> int:
    report = backfill()
    print(json.dumps(report, indent=2))
    has_anomalies = any(
        report[key]
        for key in ("missing_objects", "invalid_objects", "unregistered_local_files")
    )
    return 1 if has_anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
