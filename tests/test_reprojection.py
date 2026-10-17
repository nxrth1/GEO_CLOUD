import pytest

from geospatial.operations import reproject_feature_collection


def test_reprojects_collection_to_caller_supplied_utm_crs():
    collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "point-1",
            "properties": {"name": "test"},
            "geometry": {"type": "Point", "coordinates": [9, 0]},
        }],
    }

    result = reproject_feature_collection(collection, target_crs="EPSG:32632")
    coordinates = result["features"][0]["geometry"]["coordinates"]

    assert coordinates[0] == pytest.approx(500000, abs=1)
    assert coordinates[1] == pytest.approx(0, abs=1)
    assert result["features"][0]["properties"] == {"name": "test"}
    assert result["features"][0]["id"] == "point-1"
    assert collection["features"][0]["geometry"]["coordinates"] == [9, 0]


def test_keeps_null_geometry_and_removes_stale_bounding_boxes():
    collection = {
        "type": "FeatureCollection",
        "bbox": [-1, -1, 1, 1],
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": None,
            "bbox": [-1, -1, 1, 1],
        }],
    }

    result = reproject_feature_collection(collection, target_crs="EPSG:3857")

    assert result["features"][0]["geometry"] is None
    assert "bbox" not in result
    assert "bbox" not in result["features"][0]


def test_reprojects_wgs84_source_coordinates():
    collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [1, 1]},
        }],
    }

    result = reproject_feature_collection(collection, target_crs="EPSG:3857")

    assert result["features"][0]["geometry"]["coordinates"][0] == pytest.approx(
        111319.49, abs=0.1
    )


def test_rejects_non_feature_collection_input():
    with pytest.raises(ValueError, match="FeatureCollection"):
        reproject_feature_collection(
            {"type": "Feature", "geometry": None},
            target_crs="EPSG:3857"
        )
