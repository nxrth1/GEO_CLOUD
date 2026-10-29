"""Metadata extraction for GeoJSON datasets."""
from hashlib import sha256
from typing import Any

from shapely.geometry import shape


def sanitize_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    # Treat both POSIX and Windows path separators as untrusted user input.
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    basename = "".join(char for char in basename if char >= " " and char != "\x7f")
    basename = basename.strip()[:255]
    return basename or None


def normalized_dataset_name(name: str | None, original_filename: str | None = None) -> str | None:
    candidate = name.strip() if isinstance(name, str) else ""
    if not candidate and original_filename:
        candidate = original_filename.rsplit(".", 1)[0] if "." in original_filename else original_filename
    candidate = "".join(char for char in candidate if char >= " " and char != "\x7f")
    candidate = candidate.strip()
    if not candidate:
        return None
    return candidate[:255]


def calculate_geojson_metadata(
    geojson: dict[str, Any], *, content: bytes
) -> dict[str, Any]:
    feature_list = geojson.get("features")
    features = feature_list if isinstance(feature_list, list) else []
    geometry_types: set[str] = set()
    bounds: list[float] | None = None

    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        try:
            parsed = shape(geometry)
        except (TypeError, ValueError):
            continue
        if parsed.is_empty:
            continue
        geometry_types.add(parsed.geom_type)
        min_x, min_y, max_x, max_y = parsed.bounds
        if bounds is None:
            bounds = [min_x, min_y, max_x, max_y]
        else:
            bounds = [
                min(bounds[0], min_x),
                min(bounds[1], min_y),
                max(bounds[2], max_x),
                max(bounds[3], max_y),
            ]

    declared_crs = geojson.get("crs")
    crs_name = None
    if isinstance(declared_crs, dict):
        properties = declared_crs.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get("name"), str):
            crs_name = properties["name"][:128]
    elif isinstance(declared_crs, str):
        crs_name = declared_crs[:128]
    if crs_name is None:
        # RFC 7946 GeoJSON uses WGS 84 longitude/latitude coordinates.
        crs_name = "OGC:CRS84"

    return {
        "format": "geojson",
        "content_type": "application/geo+json",
        "size_bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "feature_count": len(features),
        "geometry_types": sorted(geometry_types),
        "bbox": bounds,
        "crs": crs_name,
    }
