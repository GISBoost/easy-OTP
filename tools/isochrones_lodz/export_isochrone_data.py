"""export_isochrone_data.py -- pack computed isochrones into per-origin GeoJSON
files + manifest.json for the izochrony-lodz web map.

Mirrors mapy-analizy/odstepy-przystankow/export_odstepy_przystankow.py's split:
QGIS (native:simplifygeometries, run separately via qgis-mcp) does the heavy
geometry simplification, this script only slims properties, rounds coordinate
precision (5 decimals ~= 1.1m, plenty for city-scale display, meaningfully
shrinks GeoJSON text size beyond what simplification alone saves), splits by
origin so the browser fetches one small file per hovered/clicked point, and
builds manifest.json.

Usage: py export_isochrone_data.py <variant: static|rt>

Input:  <variant>_isochrones_ogr.geojson (from the ogr2ogr simplify step --
        see README: ogr2ogr -simplify + -lco COORDINATE_PRECISION, run
        directly rather than through qgis-mcp, which choked on a dataset
        this size -- see README decision log)
        lodz_origins_500.csv (id, lon, lat)
Output: data/<variant>/<origin_id>.geojson (one per origin)
        data/manifest.json (written/updated after both variants are exported)
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

HOURS = list(range(6, 23))  # 06:00..22:00, matches compute_isochrones.R
CUTOFFS = [15, 30, 45]

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"


def load_origins() -> dict[str, tuple[float, float]]:
    origins = {}
    with open(HERE / "lodz_origins_500.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            origins[row["id"]] = (float(row["lon"]), float(row["lat"]))
    return origins


def export_variant(variant: str) -> set[str]:
    src = HERE / f"{variant}_isochrones_ogr.geojson"
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)

    by_origin: dict[str, list[dict]] = defaultdict(list)
    for feat in data["features"]:
        props = feat["properties"]
        origin_id = str(props["id"])
        by_origin[origin_id].append({
            "type": "Feature",
            "properties": {
                "cutoff_min": int(props["isochrone"]),
                "hour": int(props["hour"]),
            },
            "geometry": feat["geometry"],
        })

    out_dir = DATA_DIR / variant
    out_dir.mkdir(parents=True, exist_ok=True)
    for origin_id, features in by_origin.items():
        out_path = out_dir / f"{origin_id}.geojson"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": features}, fh, separators=(",", ":"))

    print(f"{variant}: wrote {len(by_origin)} origin files to {out_dir}")
    return set(by_origin.keys())


def write_manifest(variants_present: dict[str, set[str]]) -> None:
    origins = load_origins()
    lons = [lon for lon, _ in origins.values()]
    lats = [lat for _, lat in origins.values()]

    manifest = {
        "hours": HOURS,
        "cutoffs_min": CUTOFFS,
        "variants": sorted(variants_present.keys()),
        "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
        "origins": [
            {"id": oid, "lon": lon, "lat": lat}
            for oid, (lon, lat) in sorted(origins.items(), key=lambda kv: int(kv[0]))
        ],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"wrote manifest.json ({len(manifest['origins'])} origins, "
          f"{len(HOURS)} hours, variants={manifest['variants']})")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("static", "rt"):
        sys.exit("Usage: py export_isochrone_data.py <variant: static|rt>")
    variant = sys.argv[1]
    ids = export_variant(variant)

    # manifest only needs origin list (variant-independent) + which variants
    # have been exported so far -- re-derive from what's on disk each run so
    # running one variant then the other (or re-running one) keeps it correct
    present = {}
    for v in ("static", "rt"):
        vdir = DATA_DIR / v
        if vdir.exists() and any(vdir.glob("*.geojson")):
            present[v] = {p.stem for p in vdir.glob("*.geojson")}
    write_manifest(present)
