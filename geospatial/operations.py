from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform


def process_by_pipeline_intent(feature_collection_wgs84, intent="web_map", target_crs):

    INTENT_ROUTER = {
        "web_map": "EPSG:3857",       # Standard Web Mercator tile streaming (meters)
        "us_analysis": "EPSG:5070",    # NAD83 / Conic Equal Area (Best for US mainland stats)
        "europe_grid": "EPSG:3035",    # ETRS89 / LAEA coordinate tracking across Europe
        "other_uses": "target_crs"
    }
    
    target = INTENT_ROUTER.get(intent)
    if not target:
        raise ValueError(f"Unknown pipeline operational intent: '{intent}'")
        
    project = Transformer.from_crs("EPSG:4326", target, target_crs, always_xy=True).transform
    
    processed_features = []
    for f in feature_collection_wgs84.get("features", []):
        if f.get("geometry") is None:
            processed_features.append(f)
            continue
        geom = shape(f["geometry"])
        projected_geom = transform(project, geom)
        
        new_f = f.copy()
        new_f["geometry"] = mapping(projected_geom)
        processed_features.append(new_f)
        
    output = feature_collection_wgs84.copy()
    output["features"] = processed_features
    return output
