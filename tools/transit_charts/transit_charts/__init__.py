"""transit_charts - GTFS static vs GTFS-RT charts built from Family A matched positions.

Two layers, deliberately separable:

- **extraction** (`sources`, `servicedate`, `quality`, `tidy`, `extract`) - pandas plus
  `family_a`, no matplotlib, so it stays importable anywhere `family_a` runs;
- **rendering** (`render.*`) - matplotlib, only where a figure is actually drawn.

See README.md for why that split is structural rather than stylistic.
"""

__all__ = ["extract", "quality", "servicedate", "sources", "tidy"]
