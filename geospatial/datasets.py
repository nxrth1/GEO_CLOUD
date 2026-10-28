import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4


def _dataset_directory():
    configured_directory = os.environ.get("GEO_CLOUD_DATASETS_DIR")
    if configured_directory:
        return Path(configured_directory).expanduser()
    return Path(__file__).resolve().parent.parent / "data" / "datasets"


def save_dataset(geojson):
    dataset_id = uuid4()
    directory = _dataset_directory()
    directory.mkdir(parents=True, exist_ok=True)
    temporary_path = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(geojson, temporary_file)
        temporary_path.replace(directory / f"{dataset_id}.json")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return dataset_id


def load_dataset(dataset_id: UUID):
    path = _dataset_directory() / f"{dataset_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def delete_dataset(dataset_id: UUID) -> None:
    path = _dataset_directory() / f"{dataset_id}.json"
    path.unlink(missing_ok=True)
