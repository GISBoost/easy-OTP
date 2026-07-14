#!/usr/bin/env bash
set -uo pipefail   # not -e: this script must keep looping even if one record call fails

# Self-healing continuous recording for the easy-GTFS-RT Termux phone-recording
# experiment (TX-2). Meant to be run under termux-services (runit) as a
# family-a-record-<city> service — see scripts/termux/service/family-a-record-lodz/run
# for the one-city-per-service template (TX-8). Safe to re-run manually for testing.
#
# Usage: record_supervised.sh <city_id>  (city_id must match a
# ~/easy-gtfs-rt-termux/cities/<city_id>.env file - see that directory's README note).

if [ "$#" -ne 1 ]; then
  echo "Usage: record_supervised.sh <city_id>" >&2
  exit 1
fi
CITY="$1"

source "$HOME/.easy-gtfs-rt-termux.env"
source "$HOME/easy-gtfs-rt-termux/cities/${CITY}.env"
source "$HOME/easy-gtfs-rt-termux/venv/bin/activate"
termux-wake-lock

STARTUP_LOG="$HOME/easy-gtfs-rt-termux/logs/record_${CITY}_$(TZ=Europe/Warsaw date +%F).log"
mkdir -p "$HOME/easy-gtfs-rt-termux/logs"
# All city services share the one ~/easy-OTP checkout - if two start at once (e.g. every service
# restarting together at boot), a concurrent `git pull` here can lose the lock race and fail; the
# existing warning-and-continue fallback already covers that (falls back to whatever's on disk),
# so this is not otherwise handled specially.
{
  cd "$HOME/easy-OTP" && git pull --ff-only
} >> "$STARTUP_LOG" 2>&1 || echo "WARNING: git pull failed, using existing checkout" >> "$STARTUP_LOG"
cd "$HOME/easy-OTP/tools/family_a_reconstruction"

while true; do
  # 10# forces base-10: date +%H/%M zero-pads (e.g. "08"), which bash arithmetic
  # would otherwise parse as an invalid octal literal and abort the assignment.
  NOW_MIN=$(( 10#$(TZ=Europe/Warsaw date +%H) * 60 + 10#$(TZ=Europe/Warsaw date +%M) ))
  WINDOW_START=$(( 6 * 60 ))
  WINDOW_END=$(( 22 * 60 ))

  if [ "$NOW_MIN" -lt "$WINDOW_START" ] || [ "$NOW_MIN" -ge "$WINDOW_END" ]; then
    # Outside the recording window - sleep until the next 06:00.
    if [ "$NOW_MIN" -lt "$WINDOW_START" ]; then
      SLEEP_MIN=$(( WINDOW_START - NOW_MIN ))
    else
      SLEEP_MIN=$(( (24 * 60) - NOW_MIN + WINDOW_START ))
    fi
    sleep "$(( SLEEP_MIN * 60 ))"
    continue
  fi

  REMAINING_MIN=$(( WINDOW_END - NOW_MIN ))
  RECORDING_DATE="$(TZ=Europe/Warsaw date +%F)"
  TIMESTAMP="$(TZ=Europe/Warsaw date +%H%M%S)"
  OUT_DIR="$HOME/easy-gtfs-rt-termux/positions_${CITY}_${RECORDING_DATE}_${TIMESTAMP}"

  python -m family_a.cli record \
    --url "$VEHICLE_POSITIONS_URL" \
    --out-dir "$OUT_DIR" \
    --duration-min "$REMAINING_MIN" \
    --interval-sec 60 \
    >> "$HOME/easy-gtfs-rt-termux/logs/record_${CITY}_${RECORDING_DATE}.log" 2>&1

  # If family_a record exits (normal end-of-window, or killed and restarted by runit
  # calling this script again), loop back and recompute - do not assume anything
  # about *why* it exited.
done
