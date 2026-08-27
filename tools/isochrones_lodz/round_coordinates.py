"""One-off (re-runnable) cleanup: round lon/lat in already-deployed
manifest.json + boundary.geojson down to 6 decimal places (~10cm, plenty for
a 500m grid). export_isochrone_data.py now does this for new manifest.json
runs; this script re-applies it to files already sitting in mapy-analizy so
the size win doesn't wait for the next full pipeline run.

Usage: py round_coordinates.py
"""
from __future__ import annotations

import json
from pathlib import Path

SITE_DATA_DIR = Path(__file__).resolve().parents[3] / "mapy-analizy" / "izochrony-lodz" / "data"
PRECISION = 6


def round_coords(node):
    if isinstance(node, list):
        if len(node) >= 2 and all(isinstance(v, (int, float)) for v in node):
            return [round(v, PRECISION) for v in node]
        return [round_coords(v) for v in node]
    if isinstance(node, dict):
        return {k: round_coords(v) for k, v in node.items()}
    return node


def round_manifest(path: Path) -> tuple[int, int]:
    before = path.stat().st_size
    data = json.loads(path.read_text(encoding="utf-8"))
    data["bounds"] = round_coords(data["bounds"])
    data["origins"] = [{**o, "lon": round(o["lon"], PRECISION), "lat": round(o["lat"], PRECISION)} for o in data["origins"]]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return before, path.stat().st_size


def round_boundary(path: Path) -> tuple[int, int]:
    before = path.stat().st_size
    data = json.loads(path.read_text(encoding="utf-8"))
    data = round_coords(data)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return before, path.stat().st_size


def main() -> None:
    total_before = total_after = 0
    for city_dir in sorted(SITE_DATA_DIR.iterdir()):
        if not city_dir.is_dir():
            continue
        manifest = city_dir / "manifest.json"
        boundary = city_dir / "boundary.geojson"
        if manifest.exists():
            b, a = round_manifest(manifest)
            total_before += b
            total_after += a
            print(f"{city_dir.name}/manifest.json: {b} -> {a} bytes")
        if boundary.exists():
            b, a = round_boundary(boundary)
            total_before += b
            total_after += a
            print(f"{city_dir.name}/boundary.geojson: {b} -> {a} bytes")
    print(f"total: {total_before} -> {total_after} bytes ({100 * (1 - total_after / total_before):.1f}% smaller)")


if __name__ == "__main__":
    main()
