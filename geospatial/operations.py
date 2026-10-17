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

def buffer(feature_collection, projected_crs, distance_in_metres):
    if (
        not isinstance(feature_collection, dict)
        or feature_collection.get("type") != "FeatureCollection"
        or not isinstance(feature_collection.get("features"), list)
    ):
        raise ValueError("Input must be a GeoJSON FeatureCollection.")

    if projected_crs is None:
        projected_crs =  3857
        print(f"No target CRS provided. Defaulting to EPSG:{projected_crs} for metric buffering.")
    else:
         print(f" Using provided target CRS: EPSG:{projected_crs}")

    target = gpd.GeoDataFrame.from_features( feature_collection["features"], crs="EPSG:4326")
    target =  feature_collection.to_crs(projected_crs)
    target["geometry"] = target.buffer(distance_in_metres)
    target.to_file("buffered_output.geojson", driver="GeoJSON")


def clip(feature_collection, mask_collection):
    if (
        not isinstance(feature_collection, dict)
        or feature_collection.get("type") != "FeatureCollection"
        or not isinstance(feature_collection.get("features"), list)
    ):
        raise ValueError("Input must be a GeoJSON FeatureCollection.")
    if (
        not isinstance(mask_collection, dict)
        or mask_collection.get("type") != "FeatureCollection"
        or not isinstance(mask_collection.get("features"), list)
    ):
        raise ValueError("Mask must be a GeoJSON FeatureCollection.")

    first_object = gpd.GeoDataFrame.from_features(
        feature_collection["features"], crs="EPSG:4326"
    )
    second_object = gpd.GeoDataFrame.from_features(
        mask_collection["features"], crs="EPSG:4326"
    )
    clipped_target = gpd.clip(first_object, second_object)

    clipped_target.to_file("clipped_output.geojson", driver="GeoJSON")
    print("💾 Clipped output successfully saved to 'clipped_output.geojson'")

    

def difference(feature_collection, mask_collection, how="difference"):
    if (
        not isinstance(feature_collection, dict)
        or feature_collection.get("type") != "FeatureCollection"
        or not isinstance(feature_collection.get("features"), list)
    ):
        raise ValueError("Input must be a GeoJSON FeatureCollection.")
    if (
        not isinstance(mask_collection, dict)
        or mask_collection.get("type") != "FeatureCollection"
        or not isinstance(mask_collection.get("features"), list)
    ):
        raise ValueError("Mask must be a GeoJSON FeatureCollection.")

    first_object = gpd.GeoDataFrame.from_features(
        feature_collection["features"], crs="EPSG:4326"
    )
    second_object = gpd.GeoDataFrame.from_features(
        mask_collection["features"], crs="EPSG:4326"
    )
    difference_target = gpd.overlay(first_object, second_object, how= how)

    difference_target.to_file("difference_output.geojson", driver="GeoJSON")
    print("💾 Difference output successfully saved to 'difference_output.geojson'")


def dissolve(feature_collection, by=None):
    if (
        not isinstance(feature_collection, dict)
        or feature_collection.get("type") != "FeatureCollection"
        or not isinstance(feature_collection.get("features"), list)
    ):
        raise ValueError("Input must be a GeoJSON FeatureCollection.")

    first_object = gpd.GeoDataFrame.from_features(
        feature_collection["features"], crs="EPSG:4326"
    )
    dissolve_target = gpd.dissolve(first_object, by=by)

    dissolve_target.to_file("dissolve_output.geojson", driver="GeoJSON")
    print("💾 Dissolved output successfully saved to 'dissolved_output.geojson'")


def spatial_join(feature_collection, mask_collection,  how="inner", predicate="intersects"):
    if (
        not isinstance(feature_collection, dict)
        or feature_collection.get("type") != "FeatureCollection"
        or not isinstance(feature_collection.get("features"), list)
    ):
        raise ValueError("Input must be a GeoJSON FeatureCollection.")
    if (
        not isinstance(mask_collection, dict)
        or mask_collection.get("type") != "FeatureCollection"
        or not isinstance(mask_collection.get("features"), list)
    ):
        raise ValueError("Mask must be a GeoJSON FeatureCollection.")

    first_object = gpd.GeoDataFrame.from_features(
        feature_collection["features"], crs="EPSG:4326"
    )
    second_object = gpd.GeoDataFrame.from_features(
        mask_collection["features"], crs="EPSG:4326"
    )
    joined_target = gpd.sjoin(first_object, second_object, how=how, predicate=predicate)

    joined_target.to_file("joined_output.geojson", driver="GeoJSON")
    print("💾 Joined ({how}/{predicate}) successfully saved to 'joined_output.geojson'")


def nearest_feature(feature_collection, target_collection, how="left", distance_col="distance_to_target"):

    if (
        not isinstance(feature_collection, dict)
        or feature_collection.get("type") != "FeatureCollection"
        or not isinstance(feature_collection.get("features"), list)
    ):
        raise ValueError("Input feature_collection must be a GeoJSON FeatureCollection.")

    if (
        not isinstance(target_collection, dict)
        or target_collection.get("type") != "FeatureCollection"
        or not isinstance(target_collection.get("features"), list)
    ):
        raise ValueError("Target must be a GeoJSON FeatureCollection.")

    first_object = gpd.GeoDataFrame.from_features(
        feature_collection["features"], crs="EPSG:4326"
    )
    second_object = gpd.GeoDataFrame.from_features(
        target_collection["features"], crs="EPSG:4326"
    )
    
    nearest_target = gpd.sjoin_nearest(first_object, second_object, how=how, distance_col=distance_col)

    nearest_target.to_file("nearest_output.geojson", driver="GeoJSON")
    print("💾 Nearest feature output successfully saved to 'nearest_output.geojson'")