#!/bin/bash
# Sequential compute_isochrones_city.R run for all 5 accessibility_cities
# cities x both variants (static, rt) -- run after setup_city_networks.sh.
# One city/variant at a time (r5r/OTP-style, not designed for parallel graph
# builds sharing a JVM heap budget) -- same sequential convention as Lodz's
# two compute_isochrones.R invocations.
#
# Usage: ./run_city_computations.sh
set -e
export R_LIBS_USER="C:/Users/Michal/Documents/R/win-library/4.6"
RSCRIPT="/c/Program Files/R/R-4.6.1/bin/x64/Rscript.exe"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

for city in warszawa krakow gdansk poznan szczecin; do
  for variant in static rt; do
    log="${city}_${variant}_compute.log"
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting $city/$variant ==="
    "$RSCRIPT" compute_isochrones_city.R "$city" "$variant" > "$log" 2>&1
    echo "--- tail of $log ---"
    tail -5 "$log"
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') finished $city/$variant ==="
  done
done
echo "ALL DONE $(date '+%Y-%m-%d %H:%M:%S')"
