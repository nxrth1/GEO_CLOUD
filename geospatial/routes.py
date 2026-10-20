from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pyproj.exceptions import CRSError

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