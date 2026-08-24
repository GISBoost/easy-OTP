"""Per-stop pooled headway (I37's per_stop_summary), split into 4h time-of-day buckets.

Reuses the already-extracted tidy tables (out/stop_headway/cities_2026-08-13/{city}_tidy_...
csv.gz) rather than re-running `extract` from raw matched positions -- the point of the tidy
cache. `outages=[]` is the same approximation J39's `citywide_comparison` already accepts for
an already-extracted table (outages need the raw extraction report, which the cache does not
carry); stop coordinates come from the existing whole-day `{city}_2026-08-13_stops.csv` since a
stop's location does not depend on which hours are pooled.

Output: `{city}_stops_{bucket}.csv` (stop_id, n, median_headway_min, lat, lon) per city per
bucket, the same shape as the whole-day stops.csv -- input for the QGIS point-in-hex join.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from transit_charts import extract as extract_mod
from transit_charts import stop_headway

OUT_DIR = Path(__file__).resolve().parent / "out" / "stop_headway" / "cities_2026-08-13"
CITIES = ["warszawa", "krakow", "lodz", "gdansk"]
BUCKETS = [("h06_10", 6, 10), ("h10_14", 10, 14), ("h14_18", 14, 18), ("h18_22", 18, 22)]


def stop_locations_for(city: str) -> dict[str, tuple[float, float]]:
    stops = pd.read_csv(OUT_DIR / f"{city}_2026-08-13_stops.csv", dtype={"stop_id": str})
    return {row.stop_id: (row.lat, row.lon) for row in stops.itertuples() if pd.notna(row.lat)}


def main() -> None:
    for city in CITIES:
        frame = extract_mod.read_table(OUT_DIR / f"{city}_tidy_2026-08-13.csv.gz")
        locations = stop_locations_for(city)
        hour = frame.obs_local.dt.hour
        for bucket_name, start, end in BUCKETS:
            subset = frame[(hour >= start) & (hour < end)]
            stats, missing = stop_headway.per_stop_summary(subset, outages=[], stop_locations=locations, min_n=2)
            stats = stats[~stats.below_min_n]
            out_path = OUT_DIR / f"{city}_stops_{bucket_name}.csv"
            stats[["stop_id", "n", "median_headway_min", "lat", "lon"]].to_csv(out_path, index=False)
            print(f"{city} {bucket_name}: {len(stats)} stops (missing coords: {missing}) -> {out_path.name}")


if __name__ == "__main__":
    main()
