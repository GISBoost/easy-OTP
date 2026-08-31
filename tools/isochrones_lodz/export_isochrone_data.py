"""export_isochrone_data.py -- pack computed isochrones into per-origin GeoJSON
files + per-city manifest.json for the izochrony-lodz web map (now multi-city:
Lodz plus the accessibility_cities SES-study cities -- warszawa/krakow/gdansk/
poznan/szczecin, see compute_isochrones_city.R).

Mirrors mapy-analizy/odstepy-przystankow/export_odstepy_przystankow.py's split:
QGIS (native:simplifygeometries, run separately via qgis-mcp) does the heavy
geometry simplification, this script only slims properties, rounds coordinate
precision (5 decimals ~= 1.1m, plenty for city-scale display, meaningfully
shrinks GeoJSON text size beyond what simplification alone saves), splits by
origin so the browser fetches one small file per hovered/clicked point, and
builds manifest.json.

Usage: py export_isochrone_data.py <city> <variant: static|rt>
  All 6 SES-study cities have both variants -- static+realized were fetched
  fresh from a dated easy-GTFS-RT release (see setup_city_networks.sh /
  compute_isochrones_city.R's GTFS_DATE env), not reused from
  tools/accessibility_cities (whose own SES-study run only ever needed the
  realized GTFS, on a different day). GZM/Kielce are rt-only, see
  CITY_VARIANTS below.

Input:  lodz: <variant>_isochrones_ogr.geojson, lodz_origins_500.csv
        other cities: <city>_<variant>_isochrones_ogr.geojson (from the
        ogr2ogr simplify step -- see README: ogr2ogr -simplify + -lco
        COORDINATE_PRECISION, run directly rather than through qgis-mcp,
        which choked on a dataset this size -- see README decision log),
        origins read straight from
        ../accessibility_cities/<city>/<city>_hex_origins.csv (same SES-study
        grid, nothing new to generate)
Output: data/<city>/<variant>/<origin_id>.geojson (one per origin)
        data/<city>/manifest.json (written/updated after all of that city's
        variants are exported -- for lodz, run both variants' export before
        converting either to geobuf, see README pipeline step ordering)
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

HOURS = list(range(6, 23))  # 06:00..22:00, matches compute_isochrones*.R
CUTOFFS = [15, 30, 45]
CITY_VARIANTS = {
    "lodz": ("static", "rt"),
    "warszawa": ("static", "rt"),
    "krakow": ("static", "rt"),
    "gdansk": ("static", "rt"),
    "poznan": ("static", "rt"),
    "szczecin": ("static", "rt"),
    # rt only for now -- static GTFS sources found (ztm.kielce.pl, GZM CKAN)
    # but deferred to a later run, see tools/isochrones_lodz/README.md.
    "gzm": ("rt",),
    "kielce": ("rt",),
}

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"


def load_origins(city: str) -> dict[str, tuple[float, float]]:
    # Warszawa uses a coarser 1000m grid and GZM a coarser 2000m grid -- see
    # compute_isochrones_city.R for why. Must match exactly, or the manifest's
    # origin list and the actual per-origin .pbf files disagree.
    if city == "lodz":
        origins_csv = HERE / "lodz_origins_500.csv"
    elif city == "warszawa":
        origins_csv = HERE.parent / "accessibility_cities" / city / f"{city}_hex_origins_1000m.csv"
    elif city == "gzm":
        origins_csv = HERE.parent / "accessibility_cities" / city / f"{city}_hex_origins_2000m.csv"
    else:
        origins_csv = HERE.parent / "accessibility_cities" / city / f"{city}_hex_origins.csv"
    origins = {}
    with open(origins_csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            origins[row["id"]] = (float(row["lon"]), float(row["lat"]))
    return origins


def export_variant(city: str, variant: str) -> set[str]:
    src_name = f"{variant}_isochrones_ogr.geojson" if city == "lodz" else f"{city}_{variant}_isochrones_ogr.geojson"
    src = HERE / src_name
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

    out_dir = DATA_DIR / city / variant
    out_dir.mkdir(parents=True, exist_ok=True)
    for origin_id, features in by_origin.items():
        out_path = out_dir / f"{origin_id}.geojson"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": features}, fh, separators=(",", ":"))

    print(f"{city}/{variant}: wrote {len(by_origin)} origin files to {out_dir}")
    return set(by_origin.keys())


def write_manifest(city: str, variants_present: list[str]) -> None:
    origins = load_origins(city)
    lons = [lon for lon, _ in origins.values()]
    lats = [lat for _, lat in origins.values()]

    manifest = {
        "hours": HOURS,
        "cutoffs_min": CUTOFFS,
        "variants": sorted(variants_present),
        "bounds": [[round(min(lats), 6), round(min(lons), 6)], [round(max(lats), 6), round(max(lons), 6)]],
        "origins": [
            # 6 decimal places (~10cm) is plenty for a 500m grid, vs. the
            # float64 default (~15 sig figs) -- pure size cut, no visible effect.
            {"id": oid, "lon": round(lon, 6), "lat": round(lat, 6)}
            for oid, (lon, lat) in sorted(origins.items(), key=lambda kv: int(kv[0]))
        ],
    }
    city_dir = DATA_DIR / city
    city_dir.mkdir(parents=True, exist_ok=True)
    with open(city_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"wrote {city}/manifest.json ({len(manifest['origins'])} origins, "
          f"{len(HOURS)} hours, variants={manifest['variants']})")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in CITY_VARIANTS or sys.argv[2] not in CITY_VARIANTS[sys.argv[1]]:
        sys.exit(
            "Usage: py export_isochrone_data.py <city> <variant>\n"
            f"  cities/variants: { {k: list(v) for k, v in CITY_VARIANTS.items()} }"
        )
    city, variant = sys.argv[1], sys.argv[2]
    export_variant(city, variant)

    # manifest only needs origin list (variant-independent) + which of this
    # city's variants have been exported so far -- re-derive from what's on
    # disk each run so running one variant then the other (or re-running one)
    # keeps it correct. Must run before geobuf-converting any of them (convert
    # deletes the .geojson this scan looks for) -- see README.
    present = [
        v for v in CITY_VARIANTS[city]
        if (DATA_DIR / city / v).exists() and any((DATA_DIR / city / v).glob("*.geojson"))
    ]
    write_manifest(city, present)
