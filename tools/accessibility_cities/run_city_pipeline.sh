#!/bin/bash
# Full per-city pipeline after Overpass fetches + hex grid + age2029 are ready:
# prepare destination tables, run r5r twice (services, universities), analyze.
# Usage: ./run_city_pipeline.sh <city>
set -e
CITY="$1"
export R_LIBS_USER="C:/Users/Michal/Documents/R/win-library/4.6"
export JAVA_HOME="C:/Users/Michal/AppData/Local/R/cache/R/rJavaEnv/installed/windows/x64/21"
export PATH="$JAVA_HOME/bin:$PATH"
RSCRIPT="/c/Program Files/R/R-4.6.1/bin/x64/Rscript.exe"

echo "=== $CITY: prepare destinations ==="
py prepare_destinations.py "$CITY" services
py prepare_destinations.py "$CITY" universities

echo "=== $CITY: export hex origins + SES ==="
py export_hex_data.py "$CITY"

# 2026-08-22 (the release tag's date) is a SATURDAY -- confirmed live via a
# verification agent that this was silently used for all 5 non-Lodz cities,
# producing "morning rush hour" numbers actually computed on Saturday transit
# service. Fixed to the next Monday within the same GTFS's calendar coverage
# (verified present -- weekday service -- in every city's calendar.txt/
# calendar_dates.txt before this fix was applied).
echo "=== $CITY: r5r services ==="
"$RSCRIPT" run_accessibility.R "$CITY" service 24-08-2026 > "$CITY/run_service.log" 2>&1
tail -5 "$CITY/run_service.log"

echo "=== $CITY: r5r universities ==="
"$RSCRIPT" run_accessibility.R "$CITY" uni 24-08-2026 > "$CITY/run_uni.log" 2>&1
tail -5 "$CITY/run_uni.log"

echo "=== $CITY: analyze services/income ==="
py analyze_services_income.py "$CITY"

echo "=== $CITY: analyze universities ==="
py analyze_universities.py "$CITY"

echo "=== $CITY: DONE ==="
