from fastapi.testclient import TestClient
from shapely.geometry import shape

from geospatial.main import app


client = TestClient(app)


def collection(geometry):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"label": "sample"},
            "geometry": geometry,
        }],
    }


def test_buffer_route_validates_then_returns_buffer_result():
    response = client.post(
        "/buffer",
        json={
            "geojson": collection({
                "type": "Point",
                "coordinates": [0, 0],
            }),
            "projected_crs": "EPSG:32631",
            "distance_in_metres": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["validation"]["accepted"] is True
    assert len(body["result"]["features"]) == 1
    geometry = shape(body["result"]["features"][0]["geometry"])
    assert geometry.geom_type == "Polygon"
    assert body["result"]["features"][0]["properties"] == {"label": "sample"}


def test_buffer_route_rejects_invalid_geojson_before_running_operation(monkeypatch):
    def unexpected_buffer(*args, **kwargs):
        raise AssertionError("Buffer must not run for rejected GeoJSON.")

    monkeypatch.setattr("geospatial.routes.buffer", unexpected_buffer)
    response = client.post(
        "/buffer",
        json={
            "geojson": collection({
                "type": "Point",
                "coordinates": [0, 95],
            }),
            "projected_crs": "EPSG:32631",
            "distance_in_metres": 100,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["validation"]["accepted"] is False


def test_buffer_route_returns_client_error_for_invalid_crs():
    response = client.post(
        "/buffer",
        json={
            "geojson": collection({
                "type": "Point",
                "coordinates": [0, 0],
            }),
            "projected_crs": "not-a-crs",
            "distance_in_metres": 100,
        },
    )

    assert response.status_code == 422
