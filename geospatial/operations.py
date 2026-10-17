from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform


def reproject_feature_collection(feature_collection, target_crs):
    if (
        not isinstance(feature_collection, dict)
        or feature_collection.get("type") != "FeatureCollection"
        or not isinstance(feature_collection.get("features"), list)
    ):
        raise ValueError("Input must be a GeoJSON FeatureCollection.")

    project = Transformer.from_crs(
        "EPSG:4326",
        target_crs,
        always_xy=True
    ).transform

    processed_features = []
    for feature in feature_collection["features"]:
        if not isinstance(feature, dict):
            raise ValueError("Each FeatureCollection item must be an object.")

        output_feature = feature.copy()
        geometry = feature.get("geometry")
        if geometry is not None:
            projected_geometry = transform(project, shape(geometry))
            output_feature["geometry"] = mapping(projected_geometry)
        output_feature.pop("bbox", None)
        processed_features.append(output_feature)

    output = feature_collection.copy()
    output.pop("bbox", None)
    output["features"] = processed_features
    return output
