"""Post-process the QGIS-MCP-exported GeoJSON layers for the odstepy-przystankow page.

Geometry export (four_cities_layers.gpkg -> EPSG:4326 GeoJSON, whole-day and per-bucket
alike) is done via QGIS MCP (add_vector_layer + export_layer), not here -- this script only
trims each hex layer's properties down to what the page actually reads and writes
manifest.json with the headline stats, computed straight from the already-exported hex
GeoJSON so the numbers shown on the page can never drift from the geometry shown on the map.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "mapy-analizy" / "odstepy-przystankow" / "data"

CITIES = [
    ("warszawa", "Warszawa"),
    ("krakow", "Kraków"),
    ("lodz", "Łódź"),
    ("gdansk", "Gdańsk"),
]

# key -> (file suffix, label). "all" is the whole-day hex ({city}_hex.geojson, no suffix).
WINDOWS = [
    ("all", "", "Cały dzień (6-22)"),
    ("h06_10", "_h06_10", "6:00-10:00"),
    ("h10_14", "_h10_14", "10:00-14:00"),
    ("h14_18", "_h14_18", "14:00-18:00"),
    ("h18_22", "_h18_22", "18:00-22:00"),
]


def slim_hex(path: Path) -> None:
    """Idempotent: a file already slimmed by a previous run is left as-is."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["features"] and "count" in data["features"][0]["properties"]:
        return
    for feature in data["features"]:
        props = feature["properties"]
        feature["properties"] = {
            "count": props["median_headway_min_count"],
            "mean_min": round(props["median_headway_min_mean"], 2),
        }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def weighted_median(pairs: list[tuple[float, int]]) -> float:
    """Median of *value* weighted by *weight* -- half the total weight lies on each side."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    half = total / 2
    cum = 0
    for value, weight in pairs:
        cum += weight
        if cum >= half:
            return value
    return pairs[-1][0]


def bounds_of(geojson_path: Path) -> list[list[float]]:
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords):
        if isinstance(coords[0], (int, float)):
            xs.append(coords[0])
            ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    for feature in data["features"]:
        walk(feature["geometry"]["coordinates"])
    return [[min(ys), min(xs)], [max(ys), max(xs)]]  # Leaflet LatLngBounds order


def hex_stats(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = [(f["properties"]["mean_min"], f["properties"]["count"]) for f in data["features"]]
    count = sum(w for _, w in pairs)
    return {
        "count": count,
        "hex_count": len(pairs),
        "median_min": round(weighted_median(pairs), 1) if pairs else None,
    }


def main() -> None:
    manifest = {"order": [c for c, _ in CITIES], "windows": [k for k, _, _ in WINDOWS],
                "window_labels": {k: label for k, _, label in WINDOWS}, "cities": {}}
    for slug, label in CITIES:
        windows = {}
        for key, suffix, _ in WINDOWS:
            hex_path = DATA_DIR / f"{slug}_hex{suffix}.geojson"
            slim_hex(hex_path)
            windows[key] = hex_stats(hex_path)
        manifest["cities"][slug] = {
            "label": label,
            "bounds": bounds_of(DATA_DIR / f"{slug}_boundary.geojson"),
            "windows": windows,
        }
    (DATA_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for slug, label in CITIES:
        for key, _, wlabel in WINDOWS:
            s = manifest["cities"][slug]["windows"][key]
            print(f"{label} [{wlabel}]: {s['count']} przystankow w {s['hex_count']} heksach, "
                  f"mediana wazona {s['median_min']} min")


if __name__ == "__main__":
    main()
