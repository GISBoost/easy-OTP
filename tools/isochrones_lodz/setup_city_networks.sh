#!/bin/bash
# Fetches static+realized GTFS for the 5 accessibility_cities cities (from the
# 2026-08-24 Monday easy-GTFS-RT release, which bundles both for the same
# day) and lays out <city>_network_static/ + <city>_network_rt/ next to this
# script, each with a copy of that city's .osm.pbf (reused from
# tools/accessibility_cities/<city>/, same extent) + exactly one GTFS zip --
# same static/RT isolation Lodz needs (shared trip_id/stop_id would collide
# in one data_path). Run once before compute_isochrones_city.R.
#
# Usage: ./setup_city_networks.sh
set -e
GH="/c/Program Files/GitHub CLI/gh.exe"
DATE="2026-08-24"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACC_CITIES="$HERE/../accessibility_cities"

for city in warszawa krakow gdansk poznan szczecin; do
  echo "=== $city ==="
  static_dir="$HERE/${city}_network_static"
  rt_dir="$HERE/${city}_network_rt"
  mkdir -p "$static_dir" "$rt_dir"

  pbf_src="$ACC_CITIES/$city/$city.osm.pbf"
  cp -n "$pbf_src" "$static_dir/$city.osm.pbf"
  cp -n "$pbf_src" "$rt_dir/$city.osm.pbf"

  tag="${city}-realized-${DATE}-phone"
  if [ ! -f "$static_dir/${city}_static_gtfs_${DATE}.zip" ]; then
    "$GH" release download "$tag" --repo GISBoost/easy-GTFS-RT \
      --pattern "${city}_static_gtfs_${DATE}.zip" --dir "$static_dir" --clobber
  fi
  if [ ! -f "$rt_dir/${city}_realized_${DATE}_p50.zip" ]; then
    "$GH" release download "$tag" --repo GISBoost/easy-GTFS-RT \
      --pattern "${city}_realized_${DATE}_p50.zip" --dir "$rt_dir" --clobber
  fi
  echo "  static: $(ls -la "$static_dir" | grep -v '^total')"
  echo "  rt:     $(ls -la "$rt_dir" | grep -v '^total')"
done
echo "done."
