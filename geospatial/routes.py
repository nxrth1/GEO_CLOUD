import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pyproj.exceptions import CRSError

from .datasets import load_dataset, save_dataset
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


@router.post("/datasets", status_code=201)
def create_dataset(request: DatasetRequest):
    validation = _validate_geojson(request.geojson)
    try:
        dataset_id = save_dataset(request.geojson)
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="Could not save dataset.",
        ) from error

    return {"id": str(dataset_id), "validation": validation}


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: UUID):
    try:
        geojson = load_dataset(dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=500,
            detail="Could not read dataset.",
        ) from error

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
def create_dataset_buffer(dataset_id: UUID, request: DatasetBufferRequest):
    try:
        source = load_dataset(dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found.") from error

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
        result_id = save_dataset(result)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not save result dataset.") from error

    return {"id": str(result_id), "validation": validation}


class DatasetClipRequest(BaseModel):
    mask_dataset_id: UUID
    analysis_crs: str = Field(min_length=1)


@router.post("/datasets/{dataset_id}/clip", status_code=201)
def create_dataset_clip(dataset_id: UUID, request: DatasetClipRequest):
    try:
        source = load_dataset(dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found.") from error

    try:
        mask = load_dataset(request.mask_dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Mask dataset not found.") from error

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
        result_id = save_dataset(result)
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
def create_dataset_difference(dataset_id: UUID, request: DatasetDifferenceRequest):
    try:
        source = load_dataset(dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found.") from error

    try:
        mask = load_dataset(request.mask_dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Mask dataset not found.") from error

    validation = _validate_geojson(source)
    mask_validation = _validate_geojson(mask, field_name="mask")

    try:
        result = difference_operation(source, mask, request.analysis_crs)
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_dataset(result)
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
def create_dataset_dissolve(dataset_id: UUID, request: DatasetDissolveRequest):
    try:
        source = load_dataset(dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found.") from error

    validation = _validate_geojson(source)

    try:
        result = dissolve_operation(source, request.by, request.analysis_crs)
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result_id = save_dataset(result)
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
):
    try:
        source = load_dataset(dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found.") from error

    try:
        mask = load_dataset(request.mask_dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Mask dataset not found.") from error

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
        result_id = save_dataset(result)
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
):
    try:
        source = load_dataset(dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Dataset not found.") from error

    try:
        target = load_dataset(request.target_dataset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Target dataset not found.") from error

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
        result_id = save_dataset(result)
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="Could not save result dataset.",
        ) from error

    return {
        "id": str(result_id),
        "validation": {"source": validation, "target": target_validation},
    }

"""
Start with a narrow slice:

Define a persisted job record: ID, operation, input dataset IDs, options, status (queued, running, succeeded, failed), output dataset ID, and error.
Add POST /jobs to submit a job and return 202 Accepted with its ID.
Add GET /jobs/{job_id} to report status and, when complete, the result dataset ID.
Add a separate worker process to claim queued jobs, call the existing operation, save the result, and update the job.
Implement buffer first to prove the lifecycle, then add the other operations.

"""