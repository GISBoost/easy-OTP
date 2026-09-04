#!/usr/bin/env bash
set -euo pipefail

# On-demand custom recording wrapper for the easy-GTFS-RT Termux phone-recording
# experiment (TX-5). Direct-Termux equivalent of FA-9's workflow_dispatch - run
# manually by Michal when he wants a recording of arbitrary duration/interval/suffix
# without waiting for record_supervised.sh's (TX-2) fixed 6:00-22:00 window.

if [ "$#" -ne 4 ]; then
  echo "Usage: record_custom.sh <city_id> <duration_minutes> <interval_seconds> <suffix>" >&2
  exit 1
fi

CITY="$1"
DURATION_MIN="$2"
INTERVAL_SEC="$3"
SUFFIX="$4"

source "$HOME/.easy-gtfs-rt-termux.env"
source "$HOME/easy-gtfs-rt-termux/cities/${CITY}.env"
source "$HOME/easy-gtfs-rt-termux/venv/bin/activate"
termux-wake-lock

# See record_supervised.sh for why this isn't hardcoded to Europe/Warsaw - falls back to it only
# when a city's cities/<city_id>.env doesn't set TIMEZONE.
ZONE="${TIMEZONE:-Europe/Warsaw}"
RECORDING_DATE="$(TZ="$ZONE" date +%F)"
OUT_DIR="$HOME/easy-gtfs-rt-termux/positions_${CITY}_${RECORDING_DATE}_${SUFFIX}"

cd "$HOME/easy-OTP" && git pull --ff-only
cd "$HOME/easy-OTP/tools/family_a_reconstruction"

# TZ="$ZONE" prefix so the subprocess's own datetime.now() calls (recording.json, snapshot
# filenames) match the zone RECORDING_DATE above was computed in - see record_supervised.sh.
TZ="$ZONE" python -m family_a.cli record \
  --url "$VEHICLE_POSITIONS_URL" \
  --out-dir "$OUT_DIR" \
  --duration-min "$DURATION_MIN" \
  --interval-sec "$INTERVAL_SEC"

termux-wake-unlock
