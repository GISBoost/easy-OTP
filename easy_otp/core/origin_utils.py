"""Pure Python helper for the origin-point output layer (no QGIS dependency)."""


def _origin_attributes(
    router_id: str,
    lon: float,
    lat: float,
    date_s: str,
    start_s: str,
    end_s: str,
    interval_min: int,
    threshold_min: int,
    arrive_by: bool,
    walk_speed: float,
    max_walk_distance: int,
    created_at: str,
) -> dict:
    """Return ordered attribute dict for the origin-point output layer."""
    return {
        "router_id": router_id,
        "lon": round(lon, 6),
        "lat": round(lat, 6),
        "analysis_type": "static",
        "analysis_date": date_s,
        "time_start": start_s,
        "time_end": end_s,
        "interval_min": interval_min,
        "threshold_min": threshold_min,
        "arrive_by": arrive_by,
        "walk_speed": walk_speed,
        "max_walk_distance": max_walk_distance,
        "created_at": created_at,
    }
