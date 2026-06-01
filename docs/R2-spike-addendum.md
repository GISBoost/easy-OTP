# R2 Spike Addendum — corrections before implementation

This file amends `PR_easy-OTP_roadmap.md` section R2 (`DownloadTransitData`)
based on the results of Prompt 5 reconnaissance spike (2026-05-31).
Read it alongside the roadmap — it supersedes the conflicting parts of the R2 spec.

---

## Correction 1 — Geofabrik: no `geometry.bbox` field

**Roadmap assumption (step A2):** bbox read from `geometry.bbox`.  
**Reality:** Geofabrik features have only `geometry.type` + `geometry.coordinates`
(MultiPolygon). No `bbox` field exists at any level of the feature.

**Implementation change:**  
Add a private helper in `download_transit_data.py` and call it in step A2
(immediately after the feature is found, before storing the bbox for step B1):

```python
def _bbox_from_geometry(geometry: dict) -> tuple[float, float, float, float]:
    """Derive (lon_min, lat_min, lon_max, lat_max) from GeoJSON MultiPolygon."""
    lons, lats = [], []
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            for lon, lat in ring:
                lons.append(lon)
                lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)

# --- inside processAlgorithm, after finding the feature: ---
feature_geom = matched_feature["geometry"]
lon_min, lat_min, lon_max, lat_max = _bbox_from_geometry(feature_geom)
bbox = (lon_min, lat_min, lon_max, lat_max)
```

No other changes to step A2.

---

## Correction 2 — Transitland: API key required for all requests

**Roadmap assumption:** `GTFS_API_KEY` optional; unauthenticated access works
at 100 req/h.  
**Reality:** All Transitland v2 API requests return HTTP 401 without a key.
Unauthenticated access is no longer offered. Free tier requires account
registration at `https://www.transit.land` (no credit card needed).

**Implementation changes:**

1. Keep `GTFS_API_KEY` as an optional `QgsProcessingParameterString` parameter
   (UI stays the same — user may already have the key from a previous run).

2. In step B2, add an explicit pre-check before making the request:
   ```python
   api_key = parameters.get("GTFS_API_KEY", "").strip()
   if not api_key:
       raise QgsProcessingException(self.tr(
           "Transitland API requires a free API key. "
           "Sign up at https://www.transit.land/documentation/api-key "
           "and provide the key in the GTFS_API_KEY parameter."
       ))
   ```
   This gives a clear, actionable error instead of a cryptic 401.

3. Still handle HTTP 401 gracefully in the request layer (user might supply
   an expired key):
   ```python
   # existing Transitland 401 error message from roadmap table stays:
   # "Transitland API key is invalid. Get a free key at ..."
   ```

4. Update `shortHelpString()` for `GTFS_API_KEY` parameter: remove the
   phrase „without a key works at 100 req/h"; replace with:
   „Free key available at https://www.transit.land — no credit card required."

**Correct base URL (note: no `www` prefix):**
```
https://transit.land/api/v2/rest/feeds?bbox={lon_min},{lat_min},{lon_max},{lat_max}&spec=gtfs&apikey={key}
```

---

## Correction 3 — Transitland: bbox query returns continental feeds

**Roadmap assumption:** „3–5 feeds per voivodeship."  
**Reality:** A query for the Dolnośląskie bbox returned **20 feeds**, of which
only **1 was locally relevant** (MPK Wrocław). The other 19 were
continental/national aggregates (Norway, UK, Sweden, Germany, Netherlands,
Switzerland, Czech Republic, FlixBus EU, etc.) whose geometries span all of
Central Europe and therefore intersect any Polish bounding box.

Downloading all 20 feeds would pull gigabytes of irrelevant data
(UK all buses + German national rail + Norwegian aggregate…) instead of
the handful of MB for the local Polish feed.

**Implementation change — add step B2b (geometric filter):**

After receiving the Transitland feed list (step B2), filter feeds before
downloading (step B3):

```python
def _is_local_feed(
    feed: dict,
    query_bbox: tuple[float, float, float, float],
    max_area_ratio: float = 5.0,
) -> bool:
    """Return True if the feed's geometry is not vastly larger than the query bbox.

    max_area_ratio=5.0 keeps feeds up to 5× the query area (sub-regional and
    city-level feeds) and discards national/continental aggregates.
    """
    feed_version = (feed.get("feed_state") or {}).get("feed_version") or {}
    geom = feed_version.get("geometry")
    if not geom or not geom.get("coordinates"):
        return True  # no geometry info — keep conservatively

    # flatten first ring of first polygon to get approximate extent
    coords = geom["coordinates"]
    ring = coords[0] if coords else []
    # handle both Polygon (list of [lon,lat]) and nested MultiPolygon
    if ring and isinstance(ring[0], list) and isinstance(ring[0][0], list):
        ring = ring[0]  # unwrap one level for MultiPolygon
    lons = [pt[0] for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    lats = [pt[1] for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if not lons:
        return True

    feed_area = (max(lons) - min(lons)) * (max(lats) - min(lats))
    qlon_min, qlat_min, qlon_max, qlat_max = query_bbox
    query_area = (qlon_max - qlon_min) * (qlat_max - qlat_min)
    if query_area < 1e-9:
        return True

    return (feed_area / query_area) <= max_area_ratio


# --- inside processAlgorithm, between B2 and B3: ---
all_feeds = response_json.get("feeds", [])
local_feeds = [f for f in all_feeds if _is_local_feed(f, bbox)]
skipped = len(all_feeds) - len(local_feeds)
if skipped:
    feedback.pushInfo(self.tr(
        f"Skipped {skipped} feeds larger than 5× query bbox "
        "(continental/national aggregates)."
    ))
feeds_to_download = local_feeds
```

**Acceptance test update (add to R2 criteria):**  
Running `AREA_NAME='dolnoslaskie'` with a valid API key must log
„Skipped N feeds larger than 5× query bbox" and download **only** the
Wrocław MPK feed (or similar locally scoped feeds), not GB-scale national
archives.

**Note on `max_area_ratio`:** Value `5.0` is a conservative default.
A voivodeship bbox is roughly 3° × 1.5° ≈ 4.5 deg². National feeds
(e.g., Germany ~1100 deg²) have area ratio ~244×. FlixBus Europe is even
larger. A ratio of 5 keeps anything up to ~22 deg² — sufficient for
metropolitan or sub-regional feeds; too small for national ones.

---

## Correction 4 — `urls.static_current` is not always a direct .zip

**Observation:** Most `static_current` URLs are direct `.zip` links
(Wrocław, Norway, Germany gtfs.de). However, some are HTML pages:
- Switzerland: `https://data.opentransportdata.swiss/...timetable.../permalink`
- Some UK feeds: redirect chains

**Impact for v0.2:** After applying the geographic filter (Correction 3),
continental feeds with HTML links are discarded before download. For local
Polish feeds, `static_current` is a direct `.zip`.

**Minimal implementation change (B3):**  
`urllib.request.urlopen` follows HTTP redirects by default — no special
handling needed. If the downloaded bytes are not a valid ZIP archive, step B4
(`zipfile` validation) will catch it and emit a warning. No hard stop.

No additional code needed beyond what the roadmap already specifies in B4.

---

## Summary table

| # | Roadmap location | Change type | Lines of code delta |
|---|---|---|---|
| 1 | Step A2 | Add `_bbox_from_geometry()` helper + call | +12 |
| 2 | Step B2, `shortHelpString()` | Pre-check key, update help text | +8 |
| 3 | Between B2 and B3 | Add `_is_local_feed()` filter + log | +35 |
| 4 | Step B3 | No code change needed | 0 |
