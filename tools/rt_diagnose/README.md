# rt_diagnose — is the static GTFS matched to the live GTFS-RT?

Standalone diagnostic for the RT-1 "Applied 0 trip updates" blocker. **Not** part of the
plugin; it uses `gtfs-realtime-bindings` in a throwaway venv (allowed: never runs in QGIS).

## Run (Windows)

```bat
:: 1. Download the matching pair AT THE SAME TIME (same ZTM pipeline = same trip_id scheme).
curl "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGtfsRtFile?file=trip_updates.pb" -o trip_updates.pb
curl -H "Accept: application/octet-stream" "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile" -o ZTMPoznanGTFS.zip
:: (bare getGTFSFile returns the NEWEST official edition — the counterpart to the live .pb)

:: 2. Decode + compare.
py -m venv .venv
.venv\Scripts\activate
pip install gtfs-realtime-bindings
py tools\rt_diagnose\compare_rt_vs_static.py trip_updates.pb ZTMPoznanGTFS.zip
```

## Reading the VERDICT

- **EXACT-MATCH POSSIBLE** — the static feed in use was wrong; load THIS official edition in
  `RunRealtimeAccessibility` and RT will apply (exact matching, no fuzzy).
- **FUZZY POSSIBLE** — trip_ids differ but the `.pb` carries route_id + start_time; turn on
  fuzzy matching (already defaulted on) and confirm the flag nesting (fix A2).
- **NEITHER** — RT-1 is infeasible for this pairing; pivot to RT-2/RT-3.
