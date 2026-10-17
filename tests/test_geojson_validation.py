import json

from geospatial.processes import validate_json


def write_geojson(tmp_path, content, **validation_options):
    path = tmp_path / "input.geojson"
    path.write_text(json.dumps(content), encoding="utf-8")
    return validate_json(path, **validation_options)


def feature(geometry, feature_id=None):
    result = {
        "type": "Feature",
        "properties": {},
        "geometry": geometry,
    }
    if feature_id is not None:
        result["id"] = feature_id
    return result


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def point(longitude=0, latitude=0):
    return {"type": "Point", "coordinates": [longitude, latitude]}


def test_valid_collection_allows_missing_ids_and_nested_polygon_coordinates(tmp_path):
    polygon = {
        "type": "Polygon",
        "coordinates": [[
            [-1, -1],
            [1, -1],
            [1, 1],
            [-1, 1],
            [-1, -1],
        ]],
    }

    result = write_geojson(
        tmp_path,
        collection(feature(point()), feature(polygon)),
    )

    assert result["is_valid"] is True
    assert result["feature_count"] == 2
    assert result["issues"] == []


def test_reports_out_of_range_coordinates_nested_in_multipolygon(tmp_path):
    multipolygon = {
        "type": "MultiPolygon",
        "coordinates": [[[
            [0, 0],
            [2, 0],
            [2, 95],
            [0, 0],
        ]]],
    }

    result = write_geojson(tmp_path, collection(feature(multipolygon)))

    assert result["is_valid"] is False
    assert any("Latitude 95" in issue["error"] for issue in result["issues"])
    assert all(issue["feature_index"] == 0 for issue in result["issues"])


def test_reports_geometry_and_coordinate_errors_together(tmp_path):
    invalid_polygon = {
        "type": "Polygon",
        "coordinates": [[
            [0, 0],
            [2, 2],
            [0, 2],
            [2, 0],
            [0, 0],
        ]],
    }

    result = write_geojson(
        tmp_path,
        collection(
            feature(invalid_polygon, feature_id="bad-shape"),
            feature(point(longitude=181), feature_id="out-of-range"),
        ),
    )

    assert result["is_valid"] is False
    assert any("Longitude 181" in issue["error"] for issue in result["issues"])
    assert any("Self-intersection" in issue["error"] for issue in result["issues"])


def test_rejects_duplicate_ids_but_accepts_features_without_ids(tmp_path):
    result = write_geojson(
        tmp_path,
        collection(
            feature(point(), feature_id="reused"),
            feature(point()),
            feature(point(), feature_id="reused"),
        ),
    )

    duplicates = [
        issue for issue in result["issues"]
        if "Duplicate feature ID" in issue["error"]
    ]
    assert result["is_valid"] is False
    assert len(duplicates) == 1
    assert duplicates[0]["feature_index"] == 2
    assert duplicates[0]["feature_id"] == "reused"
    assert "feature index 0" in duplicates[0]["error"]


def test_collects_feature_structure_errors_and_rejects_null_geometry(tmp_path):
    malformed_feature = {
        "type": "NotFeature",
        "properties": [],
    }
    result = write_geojson(
        tmp_path,
        collection(
            "not an object",
            malformed_feature,
            feature(None),
        ),
    )

    assert result["is_valid"] is False
    assert result["feature_count"] == 3
    assert any(issue["feature_index"] == 0 for issue in result["issues"])
    assert any(issue["feature_index"] == 1 for issue in result["issues"])
    assert any(
        issue["feature_index"] == 2 and issue["error"] == "Geometry is null."
        for issue in result["issues"]
    )


def test_attribute_constraints_report_missing_type_and_allowed_value(tmp_path):
    constraints = {
        "name": {"required": True, "type": "string"},
        "status": {"type": "string", "allowed_values": ["active", "inactive"]},
        "population": {"type": "integer", "minimum": 0},
    }
    result = write_geojson(
        tmp_path,
        collection(feature(
            point(),
            feature_id="site-1"
        ) | {"properties": {"status": "unknown", "population": -2}}),
        property_constraints=constraints,
    )

    attribute_issues = [
        issue for issue in result["issues"]
        if issue["rule_id"] == "attribute_constraints"
    ]
    assert result["accepted"] is True
    assert len(attribute_issues) == 3
    assert all(issue["severity"] == "error" for issue in attribute_issues)
    assert all(issue["feature_index"] == 0 for issue in attribute_issues)
    assert all("explanation" in issue for issue in result["issues"])


def test_attribute_constraints_reject_wrong_value_type(tmp_path):
    result = write_geojson(
        tmp_path,
        collection(feature(
            point(),
            feature_id="site-1"
        ) | {"properties": {"population": "many"}}),
        property_constraints={"population": {"type": "integer"}},
    )

    attribute_issues = [
        issue for issue in result["issues"]
        if issue["rule_id"] == "attribute_constraints"
    ]
    assert len(attribute_issues) == 1
    assert "must have type integer" in attribute_issues[0]["explanation"]


def test_critical_rule_failure_rejects_and_warning_does_not(tmp_path):
    overlapping = collection(
        feature({"type": "Polygon", "coordinates": [[
            [0, 0], [2, 0], [2, 2], [0, 2], [0, 0]
        ]]}),
        feature({"type": "Polygon", "coordinates": [[
            [1, 1], [3, 1], [3, 3], [1, 3], [1, 1]
        ]]}),
    )

    critical = write_geojson(
        tmp_path,
        overlapping,
        rules={"polygon_overlap": {"severity": "critical"}},
    )
    warning = write_geojson(
        tmp_path,
        overlapping,
        rules={"polygon_overlap": {"severity": "warning"}},
    )
    disabled = write_geojson(
        tmp_path,
        overlapping,
        rules={"polygon_overlap": {"enabled": False}},
    )

    assert any(issue["rule_id"] == "polygon_overlap" for issue in critical["issues"])
    assert critical["accepted"] is False
    assert warning["is_valid"] is False
    assert warning["accepted"] is True
    assert not any(issue["rule_id"] == "polygon_overlap" for issue in disabled["issues"])


def test_geographic_coverage_reports_outside_features(tmp_path):
    coverage = {
        "type": "Polygon",
        "coordinates": [[
            [0, 0], [2, 0], [2, 2], [0, 2], [0, 0]
        ]],
    }
    result = write_geojson(
        tmp_path,
        collection(feature(point(3, 1), feature_id="outside")),
        coverage_geometry=coverage,
    )

    coverage_issues = [
        issue for issue in result["issues"]
        if issue["rule_id"] == "geographic_coverage"
    ]
    assert len(coverage_issues) == 1
    assert coverage_issues[0]["feature_id"] == "outside"
    assert result["accepted"] is False


def test_invalid_rule_configuration_returns_machine_readable_critical_issue(tmp_path):
    result = write_geojson(
        tmp_path,
        collection(feature(point())),
        rules={"geographic_coverage": {"enabled": True}},
    )

    assert result["accepted"] is False
    assert result["issues"][0]["rule_id"] == "validation_configuration"
    assert result["issues"][0]["severity"] == "critical"
