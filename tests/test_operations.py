import pytest
from shapely.geometry import shape

from geospatial.operations import (
    buffer,
    clip,
    difference,
    dissolve,
    nearest_feature,
    spatial_join,
)


def feature(geometry, properties=None, feature_id=None):
    result = {
        "type": "Feature",
        "properties": properties or {},
        "geometry": geometry,
    }
    if feature_id is not None:
        result["id"] = feature_id
    return result


def polygon(min_x, min_y, max_x, max_y):
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_x, min_y],
            [max_x, min_y],
            [max_x, max_y],
            [min_x, max_y],
            [min_x, min_y],
        ]],
    }


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def test_buffer_uses_projected_metres_and_returns_wgs84_features():
    source = collection(feature(
        {"type": "Point", "coordinates": [0, 0]},
        {"label": "origin"},
        "origin"
    ))

    result = buffer(source, "EPSG:32631", 1000)

    buffered = shape(result["features"][0]["geometry"])
    min_x, min_y, max_x, max_y = buffered.bounds
    assert buffered.geom_type == "Polygon"
    assert 0.008 < max_x < 0.01
    assert -0.01 < min_x < -0.008
    assert result["features"][0]["properties"] == {"label": "origin"}
    assert result["features"][0]["id"] == "origin"
    assert "crs" not in result


def test_buffer_rejects_geographic_crs_and_invalid_distance():
    source = collection(feature(
        {"type": "Point", "coordinates": [0, 0]}
    ))
    with pytest.raises(ValueError, match="projected CRS"):
        buffer(source, "EPSG:4326", 100)
    with pytest.raises(ValueError, match="distance_in_metres"):
        buffer(source, "EPSG:32631", -1)


def test_clip_intersects_mask_and_returns_collection():
    source = collection(feature(polygon(0, 0, 2, 2), {"kind": "land"}))
    mask = collection(feature(polygon(1, 1, 3, 3)))

    result = clip(source, mask, analysis_crs="EPSG:32631")

    assert len(result["features"]) == 1
    assert result["features"][0]["properties"] == {"kind": "land"}
    assert shape(result["features"][0]["geometry"]).area == pytest.approx(1, rel=0.02)


def test_difference_is_only_difference_and_does_not_write_a_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = collection(feature(polygon(0, 0, 2, 2), {"kind": "land"}))
    mask = collection(feature(polygon(1, 1, 3, 3)))

    result = difference(source, mask, analysis_crs="EPSG:32631")

    assert len(result["features"]) == 1
    assert shape(result["features"][0]["geometry"]).area == pytest.approx(3, rel=0.02)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(TypeError):
        difference(source, mask, how="union")


def test_dissolve_merges_features_by_attribute():
    source = collection(
        feature(polygon(0, 0, 1, 1), {"group": "a"}),
        feature(polygon(1, 0, 2, 1), {"group": "a"}),
        feature(polygon(4, 0, 5, 1), {"group": "b"}),
    )

    result = dissolve(source, by="group", analysis_crs="EPSG:32631")

    assert len(result["features"]) == 2
    grouped = {
        item["properties"]["group"]: shape(item["geometry"]).area
        for item in result["features"]
    }
    assert grouped["a"] == pytest.approx(2, rel=0.02)
    assert grouped["b"] == pytest.approx(1, rel=0.02)


def test_spatial_join_returns_matching_properties_and_target_id():
    source = collection(feature(
        {"type": "Point", "coordinates": [0.5, 0.5]},
        {"name": "source"}
    ))
    target = collection(feature(
        polygon(0, 0, 1, 1),
        {"name": "target"},
        "mask-1"
    ))

    result = spatial_join(source, target, analysis_crs="EPSG:32631")

    assert len(result["features"]) == 1
    assert result["features"][0]["properties"] == {
        "name": "source",
        "name_target": "target",
        "id_target": "mask-1",
    }
    assert shape(result["features"][0]["geometry"]).equals(
        shape(source["features"][0]["geometry"])
    )


def test_nearest_feature_calculates_distance_in_metres():
    source = collection(feature(
        {"type": "Point", "coordinates": [0, 0]},
        {"name": "source"}
    ))
    target = collection(feature(
        {"type": "Point", "coordinates": [0.009, 0]},
        {"name": "target"},
        "target-1"
    ))

    result = nearest_feature(source, target, projected_crs="EPSG:32631")
    properties = result["features"][0]["properties"]

    assert properties["distance_to_target_m"] == pytest.approx(1001.9, abs=2)
    assert properties["name_target"] == "target"
    assert properties["id_target"] == "target-1"
    assert shape(result["features"][0]["geometry"]).equals(
        shape(source["features"][0]["geometry"])
    )
