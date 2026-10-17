import json
import sqlite3
from contextlib import closing
from pathlib import Path

import fiona
from sqlalchemy import create_engine, text
from sqlalchemy.engine import url
from shapely.errors import ShapelyError
from shapely.geometry import shape
from shapely.validation import explain_validity


def _geometry_issue(geometry, feature_index, feature_id=None):
    if geometry is None:
        issue = {"feature_index": feature_index, "error": "Geometry is null."}
    else:
        try:
            geom = shape(geometry)
        except (AttributeError, KeyError, TypeError, ValueError, ShapelyError) as e:
            issue = {
                "feature_index": feature_index,
                "error": f"Could not parse geometry: {e}"
            }
        else:
            if geom.is_valid:
                return None
            issue = {
                "feature_index": feature_index,
                "geometry_type": geom.geom_type,
                "error": explain_validity(geom)
            }

    if feature_id is not None:
        issue["feature_id"] = feature_id
    return issue


def _validate_features(features):
    issues = []
    feature_count = 0

    for feature_count, feature in enumerate(features, start=1):
        issue = _geometry_issue(
            feature.get("geometry"),
            feature_count - 1,
            feature.get("id")
        )
        if issue:
            issues.append(issue)

    return {
        "is_valid": not issues,
        "feature_count": feature_count,
        "issues": issues
    }


def _validate_fiona_layer(path, layer=None):
    try:
        with fiona.open(path, layer=layer) as source:
            result = _validate_features(source)
            result["layer"] = layer or source.name
            return result
    except (fiona.errors.FionaError, OSError, ValueError) as e:
        return {"is_valid": False, "error": f"Could not read vector data: {e}"}


def validate_shapefile(shp_path):
    path = Path(shp_path)

    if path.suffix.lower() != '.shp':
        return {"is_valid": False, "error": f"Input path must end in '.shp', got '{path.suffix}'"}

    if not path.exists():
        return {"is_valid": False, "error": f"Main shapefile does not exist: {path}"}

    required_extensions = ['.shx', '.dbf']
    optional_extensions = ['.prj']
    missing_mandatory = []
    missing_optional = []

    for ext in required_extensions:
        candidates = [
            path.with_suffix(ext),
            path.with_suffix(ext.upper()),
        ]
        if not any(candidate.exists() for candidate in candidates):
            missing_mandatory.append(ext)

    for ext in optional_extensions:
        candidates = [
            path.with_suffix(ext),
            path.with_suffix(ext.upper()),
        ]
        if not any(candidate.exists() for candidate in candidates):
            missing_optional.append(ext)

    if missing_mandatory:
        return {
            "is_valid": False,
            "error": f"Missing mandatory companion files: {', '.join(missing_mandatory)}"
        }

    result = _validate_fiona_layer(path)
    if "error" in result or not result["is_valid"]:
        return result

    if missing_optional:
        result["warning"] = (
            f"Missing projection data: {', '.join(missing_optional)}. "
            "Spatial reference will be undefined."
        )

    result["message"] = "Shapefile structure and geometries are valid."
    return result


def validate_geopackage(gpkg_path):
    path = Path(gpkg_path)

    if not path.exists():
        return {"is_valid": False, "error": f"File path does not exist: {path}"}

    try:
        with closing(sqlite3.connect(path)) as conn:
            cursor = conn.cursor()
            integrity_result = cursor.execute(
                "PRAGMA integrity_check;"
            ).fetchone()[0]
            if integrity_result != "ok":
                return {
                    "is_valid": False,
                    "error": f"GeoPackage SQLite integrity check failed: {integrity_result}"
                }

            has_contents = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='gpkg_contents';"
            ).fetchone()
            has_geometry_columns = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='gpkg_geometry_columns';"
            ).fetchone()
            if not has_contents or not has_geometry_columns:
                return {
                    "is_valid": False,
                    "error": "File is a valid SQLite DB, but missing GeoPackage metadata tables."
                }

            layers = [
                row[0] for row in cursor.execute(
                    "SELECT table_name FROM gpkg_contents "
                    "WHERE data_type='features';"
                )
            ]

        layer_results = [_validate_fiona_layer(path, layer) for layer in layers]
        errors = [
            result["error"] for result in layer_results if "error" in result
        ]
        if errors:
            return {"is_valid": False, "errors": errors, "layers": layer_results}

        issues = []
        for layer_result in layer_results:
            for issue in layer_result["issues"]:
                issue["layer"] = layer_result["layer"]
                issues.append(issue)

        return {
            "is_valid": not issues,
            "feature_count": sum(
                result["feature_count"] for result in layer_results
            ),
            "layers": layer_results,
            "issues": issues,
            "message": "GeoPackage structure and geometries are valid."
        }

    except sqlite3.DatabaseError as e:
        return {"is_valid": False, "error": f"Not a valid database file or file is severely corrupted. Details: {e}"}


def validate_json(geo_path):
    path2 = Path(geo_path)

    if not path2.exists():
        return{"is_valid": False, "error": "File path does not exist."}
    try:
        data_string = path2.read_text(encoding="utf-8")
        data = json.loads(data_string)
    except json.JSONDecodeError as e:
        return {"is_valid": False, "error": f"Syntax Error: {e}"}
    except Exception as e:
        return {"is_valid": False, "error": f"Failed to read file: {e}"}


    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        return {"is_valid": False, "error": "Missing or invalid root 'type'. Expected 'FeatureCollection'."}
    
    if "features" not in data or not isinstance(data["features"], list):
        return {"is_valid": False, "error": "Missing or invalid 'features' array."}

    issues = []
    for i, feature in enumerate(data["features"]):
        if not isinstance(feature, dict):
            return {"is_valid": False, "error": f"Feature at index {i} is not a valid JSON object."}
            
        if "type" not in feature or feature["type"] != "Feature":
            return {"is_valid": False, "error": f"Feature at index {i} is missing 'type': 'Feature'."}
            
        if "properties" not in feature or not isinstance(feature["properties"], dict):
            return {"is_valid": False, "error": f"Feature at index {i} is missing a valid 'properties' dictionary."}
            
        if "geometry" not in feature:
            return {"is_valid": False, "error": f"Feature at index {i} is missing geometry."}

        issue = _geometry_issue(feature["geometry"], i, feature.get("id"))
        if issue:
            issues.append(issue)

    return {
        "is_valid": not issues,
        "feature_count": len(data["features"]),
        "issues": issues,
        "message": (
            "GeoJSON structure and geometries are valid."
            if not issues else "GeoJSON contains invalid geometries."
        )
    }


def validate_database_input(connection_string, table_name):
    try:
        url.make_url(connection_string)
    except Exception as e:
        return {"is_valid": False, "error": f"Invalid connection string format: {e}"}
    try:
        engine = create_engine(
            connection_string,
            connect_args=(
                {"connect_timeout": 5}
                if "postgres" in connection_string else {}
            )
        )

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

            table_check_query = text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_name = :t_name
            """)
            table_exists = conn.execute(
                table_check_query, {"t_name": table_name}
            ).fetchone()

            if not table_exists:
                return {"is_valid": False, "error": f"Table '{table_name}' does not exist in the database."}

            spatial_check_query = text("""
                SELECT f_geometry_column, f_table_schema
                FROM geometry_columns
                WHERE f_table_name = :t_name
                ORDER BY f_table_schema
                LIMIT 1
            """)
            spatial_column = conn.execute(
                spatial_check_query, {"t_name": table_name}
            ).fetchone()

            if not spatial_column:
                return {
                    "is_valid": True,
                    "warning": f"Table '{table_name}' exists, but no registered geometry columns were found. It will be treated as an attribute-only table."
                }

            geometry_column, schema_name = spatial_column
            preparer = conn.dialect.identifier_preparer
            qualified_table = (
                f"{preparer.quote_schema(schema_name)}."
                f"{preparer.quote(table_name)}"
            )
            quoted_geometry_column = preparer.quote(geometry_column)
            geometry_query = text(
                f"SELECT ST_AsGeoJSON({quoted_geometry_column}) "
                f"FROM {qualified_table}"
            )
            issues = []
            feature_count = 0

            for feature_count, row in enumerate(
                conn.execute(geometry_query).yield_per(1000), start=1
            ):
                try:
                    geometry = json.loads(row[0]) if row[0] is not None else None
                except (TypeError, json.JSONDecodeError) as e:
                    issue = {
                        "feature_index": feature_count - 1,
                        "error": f"Could not parse database geometry: {e}"
                    }
                else:
                    issue = _geometry_issue(geometry, feature_count - 1)
                if issue:
                    issues.append(issue)

        return {
            "is_valid": not issues,
            "feature_count": feature_count,
            "issues": issues,
            "message": (
                f"Database connection successful; geometries in "
                f"'{table_name}' are valid."
                if not issues else
                f"Table '{table_name}' contains invalid geometries."
            )
        }

    except Exception as e:
        return {"is_valid": False, "error": f"Database connection failed. Details: {e}"}

