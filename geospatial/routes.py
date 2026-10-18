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
