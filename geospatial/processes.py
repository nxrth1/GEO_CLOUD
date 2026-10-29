import json
import math
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


def _coordinate_issues(
    coordinates,
    feature_index,
    feature_id=None,
    path="coordinates"
):
    issues = []

    def walk(value, coordinate_path):
        if not isinstance(value, list):
            issues.append({
                "feature_index": feature_index,
                "error": f"{coordinate_path}: Coordinates must be an array."
            })
            return

        if len(value) >= 2 and all(
            isinstance(ordinate, (int, float)) and not isinstance(ordinate, bool)
            for ordinate in value[:2]
        ):
            longitude, latitude = value[:2]
            if not -180 <= longitude <= 180:
                issues.append({
                    "feature_index": feature_index,
                    "error": (
                        f"{coordinate_path}: Longitude {longitude} is outside "
                        "the RFC 7946 range [-180, 180]."
                    )
                })
            if not -90 <= latitude <= 90:
                issues.append({
                    "feature_index": feature_index,
                    "error": (
                        f"{coordinate_path}: Latitude {latitude} is outside "
                        "the RFC 7946 range [-90, 90]."
                    )
                })
            return

        if value and all(isinstance(item, list) for item in value):
            for index, item in enumerate(value):
                walk(item, f"{coordinate_path}[{index}]")
            return

        issues.append({
            "feature_index": feature_index,
            "error": (
                f"{coordinate_path}: A position must contain numeric "
                "longitude and latitude values."
            )
        })

    walk(coordinates, path)
    if feature_id is not None:
        for issue in issues:
            issue["feature_id"] = feature_id
    return issues


def _geometry_coordinate_issues(
    geometry,
    feature_index,
    feature_id=None,
    path="coordinates"
):
    if not isinstance(geometry, dict):
        return []

    if geometry.get("type") == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            return []
        issues = []
        for index, child in enumerate(geometries):
            issues.extend(
                _geometry_coordinate_issues(
                    child,
                    feature_index,
                    feature_id,
                    f"{path}.geometries[{index}]"
                )
            )
        return issues

    if "coordinates" not in geometry:
        return []
    return _coordinate_issues(
        geometry["coordinates"],
        feature_index,
        feature_id,
        path
    )


_VALIDATION_RULES = {
    "feature_structure": {"enabled": True, "severity": "critical"},
    "geometry_validity": {"enabled": True, "severity": "critical"},
    "coordinate_range": {"enabled": True, "severity": "critical"},
    "unique_feature_id": {"enabled": True, "severity": "error"},
    "attribute_constraints": {"enabled": False, "severity": "error"},
    "polygon_overlap": {"enabled": True, "severity": "error"},
    "geographic_coverage": {"enabled": False, "severity": "critical"},
}
_VALID_SEVERITIES = {"critical", "error", "warning", "info"}
_PROPERTY_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def _validation_issue(
    rule_id,
    severity,
    explanation,
    feature_index=None,
    feature_id=None
):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "feature_index": feature_index,
        "feature_id": feature_id,
        "explanation": explanation,
        "error": explanation,
    }


def _resolve_validation_rules(rules, property_constraints, coverage_geometry):
    if rules is not None and not isinstance(rules, dict):
        raise ValueError("rules must be an object mapping rule IDs to settings.")
    resolved = {
        rule_id: dict(settings)
        for rule_id, settings in _VALIDATION_RULES.items()
    }
    if property_constraints is not None:
        resolved["attribute_constraints"]["enabled"] = True
    if coverage_geometry is not None:
        resolved["geographic_coverage"]["enabled"] = True

    for rule_id, settings in (rules or {}).items():
        if rule_id not in resolved or not isinstance(settings, dict):
            raise ValueError(f"Invalid validation rule configuration: {rule_id!r}.")
        unknown_settings = set(settings) - {"enabled", "severity"}
        if unknown_settings:
            raise ValueError(
                f"Rule {rule_id!r} has unsupported settings: "
                f"{sorted(unknown_settings)}."
            )
        if "enabled" in settings:
            if not isinstance(settings["enabled"], bool):
                raise ValueError(f"Rule {rule_id!r} enabled must be a boolean.")
            resolved[rule_id]["enabled"] = settings["enabled"]
        if "severity" in settings:
            severity = settings["severity"]
            if not isinstance(severity, str) or severity not in _VALID_SEVERITIES:
                raise ValueError(
                    f"Rule {rule_id!r} severity must be one of "
                    f"{sorted(_VALID_SEVERITIES)}."
                )
            resolved[rule_id]["severity"] = severity

    if resolved["attribute_constraints"]["enabled"] and property_constraints is None:
        raise ValueError(
            "Property constraints are required when attribute_constraints is enabled."
        )
    if resolved["geographic_coverage"]["enabled"] and coverage_geometry is None:
        raise ValueError(
            "A GeoJSON coverage geometry is required when geographic_coverage is enabled."
        )

    return resolved


def _property_constraint_issues(
    properties,
    constraints,
    feature_index,
    feature_id,
    severity
):
    issues = []
    if not isinstance(constraints, dict):
        raise ValueError("property_constraints must be a mapping of names to rules.")

    for name, constraint in constraints.items():
        if not isinstance(name, str) or not isinstance(constraint, dict):
            raise ValueError("Each property constraint must map a name to a rule object.")

        required = constraint.get("required", False)
        if not isinstance(required, bool):
            raise ValueError(f"Property {name!r} required must be a boolean.")
        if name not in properties:
            if required:
                issues.append(_validation_issue(
                    "attribute_constraints",
                    severity,
                    f"Required property {name!r} is missing.",
                    feature_index,
                    feature_id
                ))
            continue

        value = properties[name]
        expected_type = constraint.get("type")
        if expected_type is not None:
            expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
            if not expected_types or any(
                not isinstance(type_name, str) or type_name not in _PROPERTY_TYPES
                for type_name in expected_types
            ):
                raise ValueError(
                    f"Property {name!r} type must use supported JSON types: "
                    f"{sorted(_PROPERTY_TYPES)}."
                )
            if not any(
                isinstance(value, _PROPERTY_TYPES[type_name])
                and not (
                    type_name in {"integer", "number"}
                    and isinstance(value, bool)
                )
                for type_name in expected_types
            ):
                issues.append(_validation_issue(
                    "attribute_constraints",
                    severity,
                    f"Property {name!r} must have type "
                    f"{' or '.join(expected_types)}.",
                    feature_index,
                    feature_id
                ))
                continue

        if "allowed_values" in constraint:
            allowed_values = constraint["allowed_values"]
            if not isinstance(allowed_values, list):
                raise ValueError(f"Property {name!r} allowed_values must be a list.")
            if value not in allowed_values:
                issues.append(_validation_issue(
                    "attribute_constraints",
                    severity,
                    f"Property {name!r} value {value!r} is not one of "
                    f"{allowed_values!r}.",
                    feature_index,
                    feature_id
                ))

        for bound in ("minimum", "maximum"):
            if bound in constraint:
                limit = constraint[bound]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or isinstance(limit, bool)
                    or not isinstance(limit, (int, float))
                ):
                    issues.append(_validation_issue(
                        "attribute_constraints",
                        severity,
                        f"Property {name!r} must be numeric to apply {bound}.",
                        feature_index,
                        feature_id
                    ))
                elif (
                    (bound == "minimum" and value < limit)
                    or (bound == "maximum" and value > limit)
                ):
                    issues.append(_validation_issue(
                        "attribute_constraints",
                        severity,
                        f"Property {name!r} value {value} violates "
                        f"{bound} {limit}.",
                        feature_index,
                        feature_id
                    ))
    return issues


def _validate_property_constraint_config(constraints):
    if not isinstance(constraints, dict):
        raise ValueError("property_constraints must be an object.")
    allowed_keys = {"required", "type", "allowed_values", "minimum", "maximum"}
    for name, constraint in constraints.items():
        if not isinstance(name, str) or not isinstance(constraint, dict):
            raise ValueError("Each property constraint must map a name to a rule object.")
        unknown_keys = set(constraint) - allowed_keys
        if unknown_keys:
            raise ValueError(
                f"Property {name!r} has unsupported constraints: "
                f"{sorted(unknown_keys)}."
            )
        required = constraint.get("required", False)
        if not isinstance(required, bool):
            raise ValueError(f"Property {name!r} required must be a boolean.")
        expected_type = constraint.get("type")
        if expected_type is not None:
            expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
            if not expected_types or any(
                not isinstance(type_name, str) or type_name not in _PROPERTY_TYPES
                for type_name in expected_types
            ):
                raise ValueError(
                    f"Property {name!r} type must use supported JSON types: "
                    f"{sorted(_PROPERTY_TYPES)}."
                )
        if (
            "allowed_values" in constraint
            and not isinstance(constraint["allowed_values"], list)
        ):
            raise ValueError(f"Property {name!r} allowed_values must be a list.")
        for bound in ("minimum", "maximum"):
            if bound in constraint and (
                isinstance(constraint[bound], bool)
                or not isinstance(constraint[bound], (int, float))
                or (
                    isinstance(constraint[bound], float)
                    and not math.isfinite(constraint[bound])
                )
            ):
                raise ValueError(f"Property {name!r} {bound} must be finite numeric.")
        if (
            "minimum" in constraint
            and "maximum" in constraint
            and constraint["minimum"] > constraint["maximum"]
        ):
            raise ValueError(f"Property {name!r} minimum exceeds maximum.")


def _polygonal(geometry):
    if geometry is not None and geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    return None


def _spatial_issues(features, parsed_geometries, rules, coverage_geometry):
    # These checks use planar topology on RFC 7946 longitude/latitude coordinates.
    issues = []
    overlap_rule = rules["polygon_overlap"]
    if overlap_rule["enabled"]:
        polygon_features = [
            (index, feature, _polygonal(parsed_geometries.get(index)))
            for index, feature in enumerate(features)
            if isinstance(feature, dict)
        ]
        polygon_features = [
            item for item in polygon_features if item[2] is not None
        ]
        for index, (first_index, first, first_geometry) in enumerate(polygon_features):
            for second_index, second, second_geometry in polygon_features[index + 1:]:
                if first_geometry.intersection(second_geometry).area > 0:
                    first_id = first.get("id")
                    second_id = second.get("id")
                    issues.append(_validation_issue(
                        "polygon_overlap",
                        overlap_rule["severity"],
                        (
                            f"Polygon feature {first_index}"
                            f"{f' (ID {first_id!r})' if first_id is not None else ''} "
                            f"overlaps polygon feature {second_index}"
                            f"{f' (ID {second_id!r})' if second_id is not None else ''}."
                        ),
                        second_index,
                        second_id
                    ))

    coverage_rule = rules["geographic_coverage"]
    if coverage_rule["enabled"]:
        try:
            coverage = shape(coverage_geometry)
        except (AttributeError, KeyError, TypeError, ValueError, ShapelyError) as error:
            raise ValueError(f"Invalid GeoJSON coverage geometry: {error}") from error
        if coverage.geom_type not in {"Polygon", "MultiPolygon"} or not coverage.is_valid:
            raise ValueError(
                "coverage_geometry must be a valid GeoJSON Polygon or MultiPolygon."
            )

        for feature_index, feature in enumerate(features):
            geometry = parsed_geometries.get(feature_index)
            if geometry is not None and not coverage.covers(geometry):
                feature_id = feature.get("id") if isinstance(feature, dict) else None
                issues.append(_validation_issue(
                    "geographic_coverage",
                    coverage_rule["severity"],
                    "Feature geometry is not fully within the configured coverage area.",
                    feature_index,
                    feature_id
                ))
    return issues


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


def validate_json(
    geo_path,
    *,
    property_constraints=None,
    rules=None,
    coverage_geometry=None
):
    """Validate GeoJSON data or a file against optional dataset constraints."""
    try:
        configured_rules = _resolve_validation_rules(
            rules,
            property_constraints,
            coverage_geometry
        )
        if (
            configured_rules["attribute_constraints"]["enabled"]
            and not isinstance(property_constraints, dict)
        ):
            raise ValueError("property_constraints must be an object.")
        if configured_rules["attribute_constraints"]["enabled"]:
            _validate_property_constraint_config(property_constraints)
    except (TypeError, ValueError) as error:
        issue = _validation_issue(
            "validation_configuration",
            "critical",
            str(error)
        )
        return {
            "is_valid": False,
            "accepted": False,
            "feature_count": 0,
            "issues": [issue],
            "message": "Validation configuration is invalid."
        }

    if isinstance(geo_path, dict) or not isinstance(geo_path, (str, Path)):
        # API callers may provide any decoded JSON value. Treat arrays, strings,
        # numbers and null as malformed GeoJSON instead of passing them to Path().
        data = geo_path
    else:
        path = Path(geo_path)
        if not path.exists():
            document_issue = _validation_issue(
                "geojson_document",
                "critical",
                f"File path does not exist: {path}."
            )
            return {
                "is_valid": False,
                "accepted": False,
                "feature_count": 0,
                "issues": [document_issue],
                "message": "GeoJSON file could not be read."
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError) as error:
            document_issue = _validation_issue(
                "geojson_document",
                "critical",
                f"Could not read GeoJSON: {error}"
            )
            return {
                "is_valid": False,
                "accepted": False,
                "feature_count": 0,
                "issues": [document_issue],
                "message": "GeoJSON file could not be read."
            }

    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        issue = _validation_issue(
            "feature_structure",
            configured_rules["feature_structure"]["severity"],
            "Expected a root FeatureCollection object."
        )
        return {
            "is_valid": False,
            "accepted": issue["severity"] != "critical",
            "feature_count": 0,
            "issues": [issue],
            "message": "GeoJSON root structure is invalid."
        }
    if not isinstance(data.get("features"), list):
        issue = _validation_issue(
            "feature_structure",
            configured_rules["feature_structure"]["severity"],
            "FeatureCollection must contain a features array."
        )
        return {
            "is_valid": False,
            "accepted": issue["severity"] != "critical",
            "feature_count": 0,
            "issues": [issue],
            "message": "GeoJSON root structure is invalid."
        }

    features = data["features"]
    issues = []
    seen_ids = {}
    parsed_geometries = {}
    for feature_index, feature in enumerate(features):
        if not isinstance(feature, dict):
            if configured_rules["feature_structure"]["enabled"]:
                issues.append(_validation_issue(
                    "feature_structure",
                    configured_rules["feature_structure"]["severity"],
                    f"Feature at index {feature_index} is not a JSON object.",
                    feature_index
                ))
            continue

        feature_id = feature.get("id")
        if configured_rules["feature_structure"]["enabled"]:
            if feature.get("type") != "Feature":
                issues.append(_validation_issue(
                    "feature_structure",
                    configured_rules["feature_structure"]["severity"],
                    f"Feature at index {feature_index} must have type 'Feature'.",
                    feature_index,
                    feature_id
                ))
            if not isinstance(feature.get("properties"), dict):
                issues.append(_validation_issue(
                    "feature_structure",
                    configured_rules["feature_structure"]["severity"],
                    f"Feature at index {feature_index} must have a properties object.",
                    feature_index,
                    feature_id
                ))
            if "geometry" not in feature:
                issues.append(_validation_issue(
                    "feature_structure",
                    configured_rules["feature_structure"]["severity"],
                    f"Feature at index {feature_index} is missing geometry.",
                    feature_index,
                    feature_id
                ))

        geometry = feature.get("geometry")
        if "geometry" in feature:
            if configured_rules["geometry_validity"]["enabled"]:
                geometry_issue = _geometry_issue(geometry, feature_index, feature_id)
                if geometry_issue:
                    issues.append(_validation_issue(
                        "geometry_validity",
                        configured_rules["geometry_validity"]["severity"],
                        geometry_issue["error"],
                        feature_index,
                        feature_id
                    ))
                elif geometry is not None:
                    parsed_geometries[feature_index] = shape(geometry)

            if configured_rules["coordinate_range"]["enabled"]:
                issues.extend(
                    _validation_issue(
                        "coordinate_range",
                        configured_rules["coordinate_range"]["severity"],
                        coordinate_issue["error"],
                        feature_index,
                        feature_id
                    )
                    for coordinate_issue in _geometry_coordinate_issues(
                        geometry,
                        feature_index,
                        feature_id
                    )
                )

        if configured_rules["unique_feature_id"]["enabled"] and feature_id is not None:
            if isinstance(feature_id, bool) or not isinstance(
                feature_id, (str, int, float)
            ):
                issues.append(_validation_issue(
                    "unique_feature_id",
                    configured_rules["unique_feature_id"]["severity"],
                    "Feature ID must be a string or number.",
                    feature_index,
                    feature_id
                ))
            elif isinstance(feature_id, float) and not math.isfinite(feature_id):
                issues.append(_validation_issue(
                    "unique_feature_id",
                    configured_rules["unique_feature_id"]["severity"],
                    "Feature ID must be a finite number.",
                    feature_index,
                    None
                ))
            elif feature_id in seen_ids:
                issues.append(_validation_issue(
                    "unique_feature_id",
                    configured_rules["unique_feature_id"]["severity"],
                    (
                        f"Duplicate feature ID {feature_id!r}; first seen at "
                        f"feature index {seen_ids[feature_id]}."
                    ),
                    feature_index,
                    feature_id
                ))
            else:
                seen_ids[feature_id] = feature_index

        if configured_rules["attribute_constraints"]["enabled"]:
            properties = feature.get("properties")
            if isinstance(properties, dict):
                try:
                    issues.extend(_property_constraint_issues(
                        properties,
                        property_constraints,
                        feature_index,
                        feature_id,
                        configured_rules["attribute_constraints"]["severity"]
                    ))
                except ValueError as error:
                    config_issue = _validation_issue(
                        "validation_configuration",
                        "critical",
                        str(error)
                    )
                    return {
                        "is_valid": False,
                        "accepted": False,
                        "feature_count": len(features),
                        "issues": [config_issue],
                        "message": "Validation configuration is invalid."
                    }

    try:
        issues.extend(_spatial_issues(
            features,
            parsed_geometries,
            configured_rules,
            coverage_geometry
        ))
    except (TypeError, ValueError) as error:
        config_issue = _validation_issue(
            "validation_configuration",
            "critical",
            str(error)
        )
        return {
            "is_valid": False,
            "accepted": False,
            "feature_count": len(features),
            "issues": [config_issue],
            "message": "Validation configuration is invalid."
        }

    critical_failures = [
        issue for issue in issues if issue["severity"] == "critical"
    ]
    return {
        "is_valid": not issues,
        "accepted": not critical_failures,
        "feature_count": len(features),
        "issues": issues,
        "message": (
            "GeoJSON passed validation."
            if not issues else
            "GeoJSON contains validation findings."
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
