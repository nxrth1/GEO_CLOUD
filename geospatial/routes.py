import json
import math
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from pyproj import CRS
from pyproj.exceptions import CRSError
from sqlalchemy.orm import Session

from .auth import get_current_owner
from .database import SessionLocal
from .dataset_repository import (
    get_owned_dataset,
    get_owned_datasets_for_update,
    list_owned_datasets,
    mark_dataset_storage_deleted,
    save_owned_dataset,
    soft_delete_owned_dataset,
)
from .datasets import delete_dataset, load_dataset
from .dataset_api import (
    InvalidDatasetCursor,
    dataset_metadata_response,
    decode_dataset_cursor,
    encode_dataset_cursor,
)
from .dataset_service import (
    DatasetTooLargeError,
    DatasetUploadError,
    DatasetValidationError,
    create_dataset_from_geojson,
    create_dataset_from_upload,
)
from .job_repository import create_queued_job, get_job
from .models import JobOperation
from .operations import (
    buffer,
    clip as clip_operation,
    difference as difference_operation,
    dissolve as dissolve_operation,
    nearest_feature as nearest_feature_operation,
    spatial_join as spatial_join_operation,
)
from .processes import validate_json


router = APIRouter()
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _load_dataset_record(record, *, not_found_detail: str = "Dataset not found."):
    try:
        backend = getattr(record, "storage_backend", None)
        storage_key = getattr(record, "storage_key", None)
        if backend and storage_key:
            return load_dataset(record.id, storage_backend=backend, storage_key=storage_key)
        # Compatibility for older callers/tests and rows from before metadata backfill.
        return load_dataset(record.id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=not_found_detail) from error
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=500, detail="Could not read dataset.") from error


def _load_owned_dataset(
    db: Session,
    dataset_id: UUID,
    owner_id: str,
    *,
    not_found_detail: str = "Dataset not found.",
    for_update: bool = False,
):
    record = (
        get_owned_dataset(db, dataset_id, owner_id, for_update=True)
        if for_update
        else get_owned_dataset(db, dataset_id, owner_id)
    )
    if record is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return _load_dataset_record(record, not_found_detail=not_found_detail)


def _validate_geojson(data, *, field_name="geojson"):
    validation = validate_json(data)
    if not validation["accepted"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{field_name} failed critical validation rules.",
                "validation": validation,
            },
        )
    return validation


class BufferRequest(BaseModel):
    geojson: dict[str, Any]
    projected_crs: str = Field(min_length=1)
    distance_in_metres: float = Field(ge=0)


@router.post("/buffer")
def create_buffer(request: BufferRequest):
    validation = _validate_geojson(request.geojson)

    try:
        result = buffer(
            request.geojson,
            request.projected_crs,
            request.distance_in_metres,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}


class ClipRequest(BaseModel):
    geojson: dict[str, Any]
    mask: dict[str, Any]
    analysis_crs: str = Field(min_length=1)

@router.post("/clip")
def clip(request: ClipRequest):
    validation = _validate_geojson(request.geojson)
    _validate_geojson(request.mask, field_name="mask")

    try:
        result = clip_operation(
            request.geojson,
            request.mask,
            request.analysis_crs,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}


class DifferenceRequest(BaseModel):
    geojson: dict[str, Any]
    mask: dict[str, Any]
    analysis_crs: str = Field(min_length=1)


@router.post("/difference")
def difference(request: DifferenceRequest):
    validation = _validate_geojson(request.geojson)
    _validate_geojson(request.mask, field_name="mask")

    try:
        result = difference_operation(
            request.geojson,
            request.mask,
            request.analysis_crs,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}


class DissolveRequest(BaseModel):
    geojson: dict[str, Any]
    by: str | None = None
    analysis_crs: str = Field(min_length=1)


@router.post("/dissolve")
def dissolve(request: DissolveRequest):
    validation = _validate_geojson(request.geojson)

    try:
        result = dissolve_operation(
            request.geojson,
            request.by,
            request.analysis_crs,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}


class SpatialJoinRequest(BaseModel):
    geojson: dict[str, Any]
    mask: dict[str, Any]
    how: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    analysis_crs: str = Field(min_length=1)


@router.post("/spatial_join")
def spatial_join(request: SpatialJoinRequest):
    validation = _validate_geojson(request.geojson)
    _validate_geojson(request.mask, field_name="mask")

    try:
        result = spatial_join_operation(
            request.geojson,
            request.mask,
            request.how,
            request.predicate,
            request.analysis_crs,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}


class NearestFeatureRequest(BaseModel):
    geojson: dict[str, Any]
    mask: dict[str, Any]
    projected_crs: str = Field(min_length=1)
    how: str = Field(min_length=1)
    distance_col: str = Field(min_length=1)


class DatasetRequest(BaseModel):
    geojson: dict[str, Any]
    name: str | None = Field(default=None, max_length=255)


class JobCreateRequest(BaseModel):
    operation: str = Field(min_length=1)
    source_dataset_id: UUID
    secondary_dataset_id: UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


_JOB_PARAMETER_KEYS = {
    JobOperation.BUFFER: {"projected_crs", "distance_in_metres"},
    JobOperation.CLIP: {"analysis_crs"},
    JobOperation.DIFFERENCE: {"analysis_crs"},
    JobOperation.DISSOLVE: {"analysis_crs", "by"},
    JobOperation.SPATIAL_JOIN: {"analysis_crs", "how", "predicate"},
    JobOperation.NEAREST_FEATURE: {"projected_crs", "how", "distance_col"},
}
_JOB_OPERATIONS_REQUIRING_SECONDARY_DATASET = {
    JobOperation.CLIP,
    JobOperation.DIFFERENCE,
    JobOperation.SPATIAL_JOIN,
    JobOperation.NEAREST_FEATURE,
}
_SPATIAL_JOIN_PREDICATES = {
    "intersects", "contains", "within", "touches", "crosses", "overlaps"
}


def _validate_job_parameters(
    operation: JobOperation,
    parameters: dict[str, Any],
    secondary_dataset_id: UUID | None,
) -> None:
    errors = []
    required_keys = _JOB_PARAMETER_KEYS[operation]
    missing_keys = required_keys - parameters.keys()
    extra_keys = parameters.keys() - required_keys

    if missing_keys:
        errors.append(f"Missing required parameters: {', '.join(sorted(missing_keys))}.")
    if extra_keys:
        errors.append(f"Unsupported parameters: {', '.join(sorted(extra_keys))}.")

    requires_secondary = operation in _JOB_OPERATIONS_REQUIRING_SECONDARY_DATASET
    if requires_secondary and secondary_dataset_id is None:
        errors.append(f"The {operation.value} operation requires a secondary_dataset_id.")
    elif not requires_secondary and secondary_dataset_id is not None:
        errors.append(
            f"The {operation.value} operation does not accept a secondary_dataset_id."
        )

    for key in ("analysis_crs", "projected_crs"):
        if key not in parameters:
            continue
        value = parameters[key]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} must be a non-empty CRS string.")
            continue
        try:
            crs = CRS.from_user_input(value)
        except (CRSError, TypeError, ValueError):
            errors.append(f"{key} must be a valid CRS.")
            continue
        if key == "projected_crs" and (
            crs.is_geographic
            or not crs.axis_info
            or any(
                not math.isclose(axis.unit_conversion_factor, 1.0, rel_tol=1e-9)
                for axis in crs.axis_info[:2]
            )
        ):
            errors.append(f"{key} must be a projected CRS that uses metres.")

    if "distance_in_metres" in parameters:
        distance = parameters["distance_in_metres"]
        try:
            valid_distance = (
                not isinstance(distance, bool)
                and isinstance(distance, (int, float))
                and math.isfinite(distance)
                and distance >= 0
            )
        except OverflowError:
            valid_distance = False
        if not valid_distance:
            errors.append("distance_in_metres must be a finite non-negative number.")

    if "by" in parameters and parameters["by"] is not None and not isinstance(
        parameters["by"], str
    ):
        errors.append("by must be a property name or null.")

    if "how" in parameters:
        allowed_how = {"inner", "left"}
        how = parameters["how"]
        if not isinstance(how, str) or how not in allowed_how:
            errors.append(f"how must be one of: {', '.join(sorted(allowed_how))}.")

    predicate = parameters.get("predicate")
    if "predicate" in parameters and (
        not isinstance(predicate, str)
        or predicate not in _SPATIAL_JOIN_PREDICATES
    ):
        errors.append(
            "predicate must be one of: "
            f"{', '.join(sorted(_SPATIAL_JOIN_PREDICATES))}."
        )

    if "distance_col" in parameters and (
        not isinstance(parameters["distance_col"], str)
        or not parameters["distance_col"]
    ):
        errors.append("distance_col must be a non-empty string.")

    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Invalid job request.", "errors": errors},
        )


@router.post("/jobs", status_code=202)
def create_job(
    request: JobCreateRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    try:
        operation = JobOperation(request.operation)
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported operation: {request.operation}",
        ) from error

    _validate_job_parameters(
        operation,
        request.parameters,
        request.secondary_dataset_id,
    )

    # Lock all input records in deterministic UUID order. Dataset deletion takes
    # the same row locks, preventing a delete/job-creation race.
    input_ids = [request.source_dataset_id]
    if request.secondary_dataset_id is not None:
        input_ids.append(request.secondary_dataset_id)
    records_by_id = get_owned_datasets_for_update(db, input_ids, owner_id)
    source_record = records_by_id.get(request.source_dataset_id)
    if source_record is None:
        raise HTTPException(status_code=404, detail="Source dataset not found.")
    source_dataset = _load_dataset_record(
        source_record, not_found_detail="Source dataset not found."
    )
    _validate_geojson(source_dataset, field_name="Source dataset")

    if request.secondary_dataset_id is not None:
        secondary_record = records_by_id.get(request.secondary_dataset_id)
        if secondary_record is None:
            raise HTTPException(status_code=404, detail="Secondary dataset not found.")
        secondary_dataset = _load_dataset_record(
            secondary_record, not_found_detail="Secondary dataset not found."
        )
        _validate_geojson(secondary_dataset, field_name="Secondary dataset")

    job = create_queued_job(
        db,
        operation=operation,
        owner_id=owner_id,
        source_dataset_id=request.source_dataset_id,
        secondary_dataset_id=request.secondary_dataset_id,
        parameters=request.parameters,
    )
    db.commit()
    db.refresh(job)

    return {
        "id": str(job.id),
        "operation": job.operation.value,
        "status": job.status.value,
    }


@router.get("/jobs/{job_id}")
def get_job_status(
    job_id: UUID,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    job = get_job(db, job_id, owner_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    return {
        "id": str(job.id),
        "operation": job.operation.value,
        "status": job.status.value,
        "source_dataset_id": str(job.source_dataset_id),
        "secondary_dataset_id": str(job.secondary_dataset_id) if job.secondary_dataset_id else None,
        "result_dataset_id": str(job.result_dataset_id) if job.result_dataset_id else None,
        "parameters": job.parameters,
        "error_message": job.error_message,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _upload_error_response(error: Exception):
    if isinstance(error, DatasetTooLargeError):
        return HTTPException(status_code=413, detail=str(error))
    if isinstance(error, DatasetValidationError):
        return HTTPException(
            status_code=422,
            detail={
                "message": "Dataset failed critical validation rules.",
                "validation": error.validation,
            },
        )
    if isinstance(error, DatasetUploadError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="Could not save dataset.")


@router.post("/datasets", status_code=201)
def create_dataset(
    request: DatasetRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    try:
        record, validation, _ = create_dataset_from_geojson(
            db, request.geojson, owner_id, name=request.name
        )
    except (DatasetTooLargeError, DatasetValidationError, DatasetUploadError) as error:
        raise _upload_error_response(error) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not save dataset.") from error
    return {**dataset_metadata_response(record), "validation": validation}


@router.post("/datasets/upload", status_code=201)
def upload_dataset(
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=255),
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    try:
        record, validation = create_dataset_from_upload(
            db,
            file.file,
            owner_id,
            name=name,
            original_filename=file.filename,
        )
    except (DatasetTooLargeError, DatasetValidationError, DatasetUploadError) as error:
        raise _upload_error_response(error) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not save dataset.") from error
    finally:
        file.file.close()
    return {**dataset_metadata_response(record), "validation": validation}


@router.get("/datasets")
def list_datasets(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    try:
        decoded_cursor = decode_dataset_cursor(cursor)
    except InvalidDatasetCursor as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    before_created_at, before_id = decoded_cursor or (None, None)
    records, has_more = list_owned_datasets(
        db,
        owner_id,
        limit=limit,
        before_created_at=before_created_at,
        before_id=before_id,
    )
    next_cursor = encode_dataset_cursor(records[-1]) if has_more and records else None
    return {
        "items": [dataset_metadata_response(record) for record in records],
        "next_cursor": next_cursor,
    }


@router.get("/datasets/{dataset_id}/metadata")
def get_dataset_metadata(
    owner_id: ,
    dataset_id: UUID,
    redis_client: ,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):

    cache_key = f"dataset:{owner_id}:{dataset_id}"
    cached = redis_client.get(cache_key)

    if cached is not None:
        return json.loads(cached)

    record = get_owned_dataset(db, dataset_id, owner_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return dataset_metadata_response(record)

    metadata = dataset_metadata_response(record)

    redis_client.set(
        cache_key,
        json.dumps(metadata),
        ex=60
    )


@router.delete("/datasets/{dataset_id}")
def delete_owned_dataset_route(
    dataset_id: UUID,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    record, deleted = soft_delete_owned_dataset(db, dataset_id, owner_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if not deleted:
        raise HTTPException(
            status_code=409,
            detail="Dataset is currently used by a queued or running job.",
        )

    backend = getattr(record, "storage_backend", "local")
    storage_key = getattr(record, "storage_key", None) or f"{record.id}.json"
    try:
        delete_dataset(
            record.id,
            storage_backend=backend,
            storage_key=storage_key,
        )
    except Exception:
        logger.exception("Dataset marked deleted but storage cleanup is pending: %s", record.id)
        return JSONResponse(
            status_code=202,
            content={"id": str(record.id), "status": "deleted_cleanup_pending"},
        )

    try:
        mark_dataset_storage_deleted(db, record.id, backend, storage_key)
    except Exception:
        # The file is gone, and the worker can safely retry the idempotent delete
        # and record completion in PostgreSQL.
        logger.exception("Dataset storage removed; cleanup marker update is pending: %s", record.id)
        return JSONResponse(
            status_code=202,
            content={"id": str(record.id), "status": "deleted_cleanup_pending"},
        )
    return Response(status_code=204)


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: UUID,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    geojson = _load_owned_dataset(db, dataset_id, owner_id)
    return {"id": str(dataset_id), "geojson": geojson}


@router.post("/nearest_feature")
def nearest_feature(request: NearestFeatureRequest):
    validation = _validate_geojson(request.geojson)
    _validate_geojson(request.mask, field_name="mask")

    try:
        result = nearest_feature_operation(
            request.geojson,
            request.mask,
            request.projected_crs,
            request.how,
            request.distance_col,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}

class DatasetBufferRequest(BaseModel):
    projected_crs: str = Field(min_length=1)
    distance_in_metres: float = Field(ge=0)

@router.post("/datasets/{dataset_id}/buffer", status_code=201)
def create_dataset_buffer(
    dataset_id: UUID,
    request: DatasetBufferRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    source = _load_owned_dataset(db, dataset_id, owner_id)

    validation = _validate_geojson(source)

    try:
        result = buffer(
            source,
            request.projected_crs,
            request.distance_in_metres,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_owned_dataset(db, result, owner_id)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not save result dataset.") from error

    return {"id": str(result_id), "validation": validation}


class DatasetClipRequest(BaseModel):
    mask_dataset_id: UUID
    analysis_crs: str = Field(min_length=1)


@router.post("/datasets/{dataset_id}/clip", status_code=201)
def create_dataset_clip(
    dataset_id: UUID,
    request: DatasetClipRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    source = _load_owned_dataset(db, dataset_id, owner_id)
    mask = _load_owned_dataset(
        db,
        request.mask_dataset_id,
        owner_id,
        not_found_detail="Mask dataset not found.",
    )

    validation = _validate_geojson(source)
    mask_validation = _validate_geojson(mask, field_name="mask")

    try:
        result = clip_operation(
            source,
            mask,
            request.analysis_crs,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_owned_dataset(db, result, owner_id)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not save result dataset.") from error

    return {
        "id": str(result_id),
        "validation": {"source": validation, "mask": mask_validation},
    }


class DatasetDifferenceRequest(BaseModel):
    mask_dataset_id: UUID
    analysis_crs: str = Field(min_length=1)


@router.post("/datasets/{dataset_id}/difference", status_code=201)
def create_dataset_difference(
    dataset_id: UUID,
    request: DatasetDifferenceRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    source = _load_owned_dataset(db, dataset_id, owner_id)
    mask = _load_owned_dataset(
        db,
        request.mask_dataset_id,
        owner_id,
        not_found_detail="Mask dataset not found.",
    )

    validation = _validate_geojson(source)
    mask_validation = _validate_geojson(mask, field_name="mask")

    try:
        result = difference_operation(source, mask, request.analysis_crs)
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_owned_dataset(db, result, owner_id)
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="Could not save result dataset.",
        ) from error

    return {
        "id": str(result_id),
        "validation": {"source": validation, "mask": mask_validation},
    }


class DatasetDissolveRequest(BaseModel):
    by: str | None = None
    analysis_crs: str = Field(min_length=1)


@router.post("/datasets/{dataset_id}/dissolve", status_code=201)
def create_dataset_dissolve(
    dataset_id: UUID,
    request: DatasetDissolveRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    source = _load_owned_dataset(db, dataset_id, owner_id)

    validation = _validate_geojson(source)

    try:
        result = dissolve_operation(source, request.by, request.analysis_crs)
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_owned_dataset(db, result, owner_id)
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="Could not save result dataset.",
        ) from error

    return {"id": str(result_id), "validation": validation}


class DatasetSpatialJoinRequest(BaseModel):
    mask_dataset_id: UUID
    how: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    analysis_crs: str = Field(min_length=1)


@router.post("/datasets/{dataset_id}/spatial_join", status_code=201)
def create_dataset_spatial_join(
    dataset_id: UUID,
    request: DatasetSpatialJoinRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    source = _load_owned_dataset(db, dataset_id, owner_id)
    mask = _load_owned_dataset(
        db,
        request.mask_dataset_id,
        owner_id,
        not_found_detail="Mask dataset not found.",
    )

    validation = _validate_geojson(source)
    mask_validation = _validate_geojson(mask, field_name="mask")

    try:
        result = spatial_join_operation(
            source,
            mask,
            request.how,
            request.predicate,
            request.analysis_crs,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_owned_dataset(db, result, owner_id)
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="Could not save result dataset.",
        ) from error

    return {
        "id": str(result_id),
        "validation": {"source": validation, "mask": mask_validation},
    }


class DatasetNearestFeatureRequest(BaseModel):
    target_dataset_id: UUID
    projected_crs: str = Field(min_length=1)
    how: str = Field(min_length=1)
    distance_col: str = Field(min_length=1)


@router.post("/datasets/{dataset_id}/nearest_feature", status_code=201)
def create_dataset_nearest_feature(
    dataset_id: UUID,
    request: DatasetNearestFeatureRequest,
    db: Session = Depends(get_db),
    owner_id: str = Depends(get_current_owner),
):
    source = _load_owned_dataset(db, dataset_id, owner_id)
    target = _load_owned_dataset(
        db,
        request.target_dataset_id,
        owner_id,
        not_found_detail="Target dataset not found.",
    )

    validation = _validate_geojson(source)
    target_validation = _validate_geojson(target, field_name="target")

    try:
        result = nearest_feature_operation(
            source,
            target,
            request.projected_crs,
            request.how,
            request.distance_col,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_owned_dataset(db, result, owner_id)
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="Could not save result dataset.",
        ) from error

    return {
        "id": str(result_id),
        "validation": {"source": validation, "target": target_validation},
    }
