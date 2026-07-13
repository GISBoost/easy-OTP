#!/usr/bin/env bash
set -euo pipefail

# On-demand custom recording wrapper for the easy-GTFS-RT Termux phone-recording
# experiment (TX-5). Direct-Termux equivalent of FA-9's workflow_dispatch and
# OR-5's oracle_record_custom.sh - run manually by Michal when he wants a
# recording of arbitrary duration/interval/suffix without waiting for
# record_supervised.sh's (TX-2) fixed 6:00-22:00 window.

if [ "$#" -ne 3 ]; then
  echo "Usage: record_custom.sh <duration_minutes> <interval_seconds> <suffix>" >&2
  exit 1
fi

DURATION_MIN="$1"
INTERVAL_SEC="$2"
SUFFIX="$3"

source "$HOME/.easy-gtfs-rt-termux.env"
source "$HOME/easy-gtfs-rt-termux/venv/bin/activate"
termux-wake-lock

RECORDING_DATE="$(TZ=Europe/Warsaw date +%F)"
OUT_DIR="$HOME/easy-gtfs-rt-termux/positions_${RECORDING_DATE}_${SUFFIX}"

cd "$HOME/easy-OTP" && git pull --ff-only
cd "$HOME/easy-OTP/tools/family_a_reconstruction"

python -m family_a.cli record \
  --url "$LODZ_VEHICLE_POSITIONS_URL" \
  --out-dir "$OUT_DIR" \
  --duration-min "$DURATION_MIN" \
  --interval-sec "$INTERVAL_SEC"

termux-wake-unlock
