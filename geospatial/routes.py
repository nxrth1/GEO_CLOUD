from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pyproj.exceptions import CRSError

from .operations import buffer
from .processes import validate_json


router = APIRouter()


class BufferRequest(BaseModel):
    geojson: dict[str, Any]
    projected_crs: str = Field(min_length=1)
    distance_in_metres: float = Field(ge=0)


@router.post("/buffer")
def create_buffer(request: BufferRequest):
    validation = validate_json(request.geojson)
    if not validation["accepted"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "GeoJSON failed critical validation rules.",
                "validation": validation,
            },
        )

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
def clip(request:ClipRequest):
    if not validation["accepted"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "GeoJSON failed critical validation rules.",
                "validation": validation,
            },
        )

    try:
        result = clip(
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
def difference(request:DifferenceRequest):
    validation = validate_json(request.geojson)
    if not validation["accepted"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "GeoJSON failed critical validation rules.",
                "validation": validation,
            },
        )

    try:
        result = difference(
            request.geojson,
            request.mask,
            request.analysis_crs,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}


class DissolveRequest(BaseModel):
    geojson: dict[str, Any]
    by: str = Field(None)
    analysis_crs: str = Field(min_length=1)

@router.post("/dissolve")
def dissolve(request:DissolveRequest):
    validation = validate_json(request.geojson)
    if not validation["accepted"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "GeoJSON failed critical validation rules.",
                "validation": validation,
            },
        )

    try:
        result = dissolve(
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
def spatial_join(request:SpatialJoinRequest):
    validation = validate_json(request.geojson)
    if not validation["accepted"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "GeoJSON failed critical validation rules.",
                "validation": validation,
            },
        )

    try:
        result = spatial_join(
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
    distance_col: float = Field(ge=0)



@router.post("/nearest_feature")
def nearest_feature(request:NearestFeatureRequest):
    validation = validate_json(request.geojson)
    if not validation["accepted"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "GeoJSON failed critical validation rules.",
                "validation": validation,
            },
        )

    try:
        result = nearest_feature(
            request.geojson,
            request.mask,
            request.projected_crs,
            request.how,
            request.distance_col,
        )
    except (CRSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {"validation": validation, "result": result}