import json
import math
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import fiona
from sqlalchemy import create_engine, text
from sqlalchemy.engine import url
from shapely.errors import ShapelyError
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union
from shapely.validation import explain_validity


WGS84 = "EPSG:4326"


def _validate_collection(collection, name="input"):
    if (
        not isinstance(collection, dict)
        or collection.get("type") != "FeatureCollection"
        or not isinstance(collection.get("features"), list)
    ):
        raise ValueError(f"{name} must be a GeoJSON FeatureCollection.")
    if any(not isinstance(feature, dict) for feature in collection["features"]):
        raise ValueError(f"Every feature in {name} must be an object.")
    declared_crs = collection.get("crs")
    if declared_crs is not None:
        try:
            is_wgs84 = CRS.from_user_input(declared_crs).equals(CRS.from_epsg(4326))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} declares an invalid CRS.") from error
        if not is_wgs84:
            raise ValueError(f"{name} must use WGS 84 (EPSG:4326) coordinates.")


def _crs_transformer(source_crs, target_crs):
    return Transformer.from_crs(source_crs, target_crs, always_xy=True).transform


def _metric_transformer(projected_crs):
    crs = CRS.from_user_input(projected_crs)
    if crs.is_geographic or not crs.axis_info:
        raise ValueError("A projected CRS with metre units is required.")
    if any(
        not math.isclose(axis.unit_conversion_factor, 1.0, rel_tol=1e-9)
        for axis in crs.axis_info[:2]
    ):
        raise ValueError("The selected projected CRS must use metres.")
    return crs, _crs_transformer(WGS84, crs)


def _analysis_transformers(analysis_crs):
    analysis = CRS.from_user_input(analysis_crs)
    return _crs_transformer(WGS84, analysis), _crs_transformer(analysis, WGS84)


def _features_with_geometries(collection):
    for feature in collection["features"]:
        geometry = feature.get("geometry")
        yield feature, shape(geometry) if geometry is not None else None


def _output_feature(source_feature, geometry):
    feature = source_feature.copy()
    feature["geometry"] = mapping(geometry) if geometry is not None else None
    feature.pop("bbox", None)
    return feature


def _output_collection(template, features):
    result = template.copy()
    result.pop("bbox", None)
    result["features"] = features
    return result


def _new_feature(geometry, properties, feature_id=None):
    feature = {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(geometry) if geometry is not None else None,
    }
    if feature_id is not None:
        feature["id"] = feature_id
    return feature


def reproject_feature_collection(feature_collection, target_crs):
    """Reproject WGS 84 GeoJSON; the added CRS marker is a non-RFC 7946 extension."""
    _validate_collection(feature_collection)
    target = CRS.from_user_input(target_crs)
    project = _crs_transformer(WGS84, target)

    features = []
    for feature, geometry in _features_with_geometries(feature_collection):
        projected = transform(project, geometry) if geometry is not None else None
        features.append(_output_feature(feature, projected))

    output = _output_collection(feature_collection, features)
    output["crs"] = target.to_string()
    return output


def buffer(feature_collection, projected_crs, distance_in_metres):
    """Buffer WGS 84 features in a caller-selected metre-based projected CRS."""
    _validate_collection(feature_collection)
    if (
        isinstance(distance_in_metres, bool)
        or not isinstance(distance_in_metres, (int, float))
        or not math.isfinite(distance_in_metres)
        or distance_in_metres < 0
    ):
        raise ValueError("distance_in_metres must be a finite non-negative number.")

    _, to_projected = _metric_transformer(projected_crs)
    to_wgs84 = _crs_transformer(projected_crs, WGS84)
    features = []
    for feature, geometry in _features_with_geometries(feature_collection):
        buffered = None
        if geometry is not None:
            projected = transform(to_projected, geometry)
            buffered = transform(to_wgs84, projected.buffer(distance_in_metres))
        features.append(_output_feature(feature, buffered))
    return _output_collection(feature_collection, features)


def clip(feature_collection, mask_collection, analysis_crs=WGS84):
    """Intersect input features with the mask union in a planar analysis CRS.

    Select a suitable projected analysis_crs for the data's region. Output
    coordinates are transformed back to WGS 84.
    """
    _validate_collection(feature_collection)
    _validate_collection(mask_collection, "mask")
    to_analysis, to_wgs84 = _analysis_transformers(analysis_crs)
    masks = [
        transform(to_analysis, geometry)
        for _, geometry in _features_with_geometries(mask_collection)
        if geometry is not None
    ]
    if not masks:
        return _output_collection(feature_collection, [])

    mask = unary_union(masks)
    features = []
    for feature, geometry in _features_with_geometries(feature_collection):
        if geometry is None:
            continue
        clipped = transform(to_analysis, geometry).intersection(mask)
        if not clipped.is_empty:
            features.append(_output_feature(feature, transform(to_wgs84, clipped)))
    return _output_collection(feature_collection, features)


def difference(feature_collection, mask_collection, analysis_crs=WGS84):
    """Subtract the mask union in a planar analysis CRS; return WGS 84 output."""
    _validate_collection(feature_collection)
    _validate_collection(mask_collection, "mask")
    to_analysis, to_wgs84 = _analysis_transformers(analysis_crs)
    masks = [
        transform(to_analysis, geometry)
        for _, geometry in _features_with_geometries(mask_collection)
        if geometry is not None
    ]
    features = []
    if not masks:
        return _output_collection(
            feature_collection,
            [
                _output_feature(
                    feature,
                    transform(to_wgs84, transform(to_analysis, geometry))
                    if geometry is not None else None
                )
                for feature, geometry in _features_with_geometries(feature_collection)
            ]
        )

    mask = unary_union(masks)
    for feature, geometry in _features_with_geometries(feature_collection):
        if geometry is None:
            continue
        result = transform(to_analysis, geometry).difference(mask)
        if not result.is_empty:
            features.append(_output_feature(feature, transform(to_wgs84, result)))
    return _output_collection(feature_collection, features)


def dissolve(feature_collection, by=None, analysis_crs=WGS84):
    """Merge geometries in a planar analysis CRS, optionally by a property."""
    _validate_collection(feature_collection)
    if by is not None and not isinstance(by, str):
        raise ValueError("by must be a property name or None.")
    to_analysis, to_wgs84 = _analysis_transformers(analysis_crs)
    groups = {}
    for feature, geometry in _features_with_geometries(feature_collection):
        if geometry is not None:
            geometry = transform(to_analysis, geometry)
        key = (feature.get("properties") or {}).get(by) if by else None
        try:
            hash(key)
        except TypeError as error:
            raise ValueError("The dissolve property value must be scalar.") from error
        groups.setdefault(key, []).append((feature, geometry))

    output_features = []
    for key, entries in groups.items():
        geometries = [geometry for _, geometry in entries if geometry is not None]
        merged = unary_union(geometries) if geometries else None
        if merged is not None:
            merged = transform(to_wgs84, merged)
        properties = {by: key} if by else {}
        output_features.append(_new_feature(merged, properties))
    return _output_collection(feature_collection, output_features)


def spatial_join(
    feature_collection,
    mask_collection,
    how="inner",
    predicate="intersects",
    analysis_crs=WGS84
):
    """Join feature properties in a planar analysis CRS using a predicate."""
    _validate_collection(feature_collection)
    _validate_collection(mask_collection, "mask")
    if how not in {"inner", "left"}:
        raise ValueError("how must be either 'inner' or 'left'.")
    if predicate not in {
        "intersects", "contains", "within", "touches", "crosses", "overlaps"
    }:
        raise ValueError("Unsupported spatial join predicate.")
    to_analysis, _ = _analysis_transformers(analysis_crs)

    output_features = []
    for source, source_geometry in _features_with_geometries(feature_collection):
        matches = []
        if source_geometry is not None:
            source_analysis = transform(to_analysis, source_geometry)
            for target, target_geometry in _features_with_geometries(mask_collection):
                target_analysis = (
                    transform(to_analysis, target_geometry)
                    if target_geometry is not None else None
                )
                if target_analysis is not None and getattr(
                    source_analysis, predicate
                )(target_analysis):
                    matches.append(target)
        if not matches and how == "left":
            output_features.append(_output_feature(source, source_geometry))
            continue
        for target in matches:
            properties = dict(source.get("properties") or {})
            for key, value in (target.get("properties") or {}).items():
                output_key = f"{key}_target" if key in properties else key
                properties[output_key] = value
            if target.get("id") is not None:
                properties["id_target"] = target["id"]
            output_features.append(
                _new_feature(source_geometry, properties, source.get("id"))
            )
    return _output_collection(feature_collection, output_features)


def nearest_feature(
    feature_collection,
    target_collection,
    projected_crs,
    how="left",
    distance_col="distance_to_target_m"
):
    """Find nearest targets using a suitable projected CRS with metre units."""
    _validate_collection(feature_collection)
    _validate_collection(target_collection, "target")
    if how not in {"left", "inner"}:
        raise ValueError("how must be either 'left' or 'inner'.")
    if not isinstance(distance_col, str) or not distance_col:
        raise ValueError("distance_col must be a non-empty string.")

    _, to_projected = _metric_transformer(projected_crs)
    projected_targets = [
        (feature, transform(to_projected, geometry))
        for feature, geometry in _features_with_geometries(target_collection)
        if geometry is not None and not geometry.is_empty
    ]
    output_features = []
    for source, source_geometry in _features_with_geometries(feature_collection):
        if source_geometry is None or source_geometry.is_empty or not projected_targets:
            if how == "left":
                output_features.append(_output_feature(source, source_geometry))
            continue

        projected_source = transform(to_projected, source_geometry)
        target, target_geometry = min(
            projected_targets,
            key=lambda item: projected_source.distance(item[1])
        )
        properties = dict(source.get("properties") or {})
        properties[distance_col] = projected_source.distance(target_geometry)
        for key, value in (target.get("properties") or {}).items():
            output_key = f"{key}_target" if key in properties else key
            properties[output_key] = value
        if target.get("id") is not None:
            properties["id_target"] = target["id"]
        output_features.append(
            _new_feature(source_geometry, properties, source.get("id"))
        )
    return _output_collection(feature_collection, output_features)
