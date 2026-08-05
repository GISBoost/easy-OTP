#!/usr/bin/env bash
set -uo pipefail   # not -e: one city's failure shouldn't stop the others

# Monthly archival of raw GPS snapshot recordings (positions_<city>_<date>_* directories) to a
# GitHub Release, so they don't accumulate on the phone forever - sweep_and_upload.sh (TX-3)
# never deletes them, only the transient per-day upload zip, so without this script the source
# directories under $WORK_DIR grow unbounded (this is exactly what forced a one-off manual
# archival of the July 2026 backlog - see raw-snapshots-2026-07 on GISBoost/easy-GTFS-RT).
#
# Self-gates per city, per its own TIMEZONE (same reasoning and pattern as sweep_and_upload.sh's
# Gate 1/Gate 2 - see that script's header comment): a single fixed-time monthly trigger can't
# safely cover cities in different timezones, the same lesson this project already learned the
# hard way once (see the build workflow's removed 22:15-Europe/Warsaw schedule fallback, and this
# directory's README "Known gotchas" entry about it). So instead, this script is meant to run
# once a day (see the crontab entry in scripts/termux/README.md) and is a silent no-op for
# essentially the whole month, per city.
#
# Deliberately a separate script from sweep_and_upload.sh, not folded into its existing per-city
# loop, even though that would let it reuse the ZONE already resolved there - keeps this monthly,
# non-critical concern from adding any risk to the daily upload/dispatch path that actually
# matters every single day. Small self-contained scripts duplicating a handful of lines is this
# project's established convention (see sweep_and_upload.sh's own header comment, and
# family_a/recorder.py's "intentional duplicate" docstring) - the per-city TIMEZONE resolution
# below is one such deliberate duplicate.
#
# Compression: solid tar+xz (tar piped into xz over every session directory for that
# city/month at once), never per-file. Consecutive GTFS-RT snapshots are near-identical (same
# vehicles/trips/routes, only position/timestamp drift) - a per-file compressor can't exploit
# that redundancy across file boundaries and loses roughly 4x the ratio a single solid stream
# gets (measured packing the July 2026 backlog by hand on a PC: solid xz -9e got the raw .pb
# data down to ~9% of its original size; non-solid zip - the format sweep_and_upload.sh itself
# uses for the daily transient upload, fine for that different purpose - only ~35%).
#
# `-6 -T0` (multi-threaded, not `-9e`), a deliberate choice for THIS hardware after benchmarking
# directly on the phone (MediaTek MT6769Z, 8 cores, ~4GB RAM) against real Prague data (its
# largest city): single-threaded `-9e` was still running after 24 minutes on 1.16GB and was
# killed rather than waited out; single-threaded `-6` finished a proportional sample at an
# extrapolated ~20 minutes for the same input; multi-threaded `-6 -T0` finished the full 1.16GB
# in 6m41s (6 threads, ~620MB peak RSS, comfortably under the phone's available memory) for a
# 9.8% ratio - about 2 percentage points worse than -9e's, but roughly 3x faster wall-clock than
# even single-threaded -6, let alone -9e. At that measured throughput, the full monthly backlog
# across every configured city (~25-30GB combined, per 2026-08 volumes) runs in a bit over an
# hour, not the many hours -9e would have needed - this only runs once a day for a few days each
# month, but still has to share the phone with 20+ concurrent recording processes once the day's
# 06:00 windows start, so wall-clock time matters here far more than it did compressing the same
# backlog on a PC.
#
# Safe to re-run repeatedly (cron fires this daily): a city whose previous month is already
# archived (.archived_<city>_<YYYY-MM> marker present) is a fast, silent no-op, same idempotency
# idiom as sweep_and_upload.sh's .uploaded/.dispatched markers. No notifications on failure, by
# design (see this directory's README "Notifications" section) - a failed month is simply
# retried on every subsequent day's tick until it succeeds, same reliability class as
# sweep_and_upload.sh's own upload/dispatch retries.
#
# Requires `xz-utils` (not installed by termux_provision.sh before this script existed - added
# alongside it). Uploads via the same direct-curl-to-the-REST-API pattern sweep_and_upload.sh
# already uses (no `gh` on the phone), reusing the same $GH_TOKEN.

source "$HOME/.easy-gtfs-rt-termux.env"
REPO="GISBoost/easy-GTFS-RT"
API="https://api.github.com"
WORK_DIR="$HOME/easy-gtfs-rt-termux"

# How many days into a new month this script keeps trying to catch a city's previous-month
# archival before giving up until next month. Not just "day 1" - a phone that's off, mid-reboot,
# or throttled by Android right at the month boundary (the exact class of unreliability this
# whole Termux track already has to live with - see README "Known gotchas") would otherwise miss
# the window entirely and wait a full extra month. Once a city's marker exists for the month,
# every day within (and after) this window is still a fast no-op.
CATCH_UP_DAYS=5

for CITY_ENV in "$WORK_DIR"/cities/*.env; do
  [ -f "$CITY_ENV" ] || continue
  CITY="$(basename "$CITY_ENV" .env)"

  # Reset before sourcing, same reasoning as sweep_and_upload.sh - a city whose .env doesn't set
  # TIMEZONE must not inherit whatever an earlier city in this same loop left behind.
  unset TIMEZONE
  source "$CITY_ENV"
  ZONE="${TIMEZONE:-Europe/Warsaw}"

  # Gate 1: only act within the first few local days of a new month for this city. 10# forces
  # base-10 - date +%d/+%m zero-pad (e.g. "01"), which bash arithmetic would otherwise try to
  # parse as invalid octal (the exact gotcha record_supervised.sh's own date handling already
  # had to work around - see README "Known gotchas").
  TODAY_DAY="$(TZ="$ZONE" date +%d)"
  if [ "$((10#$TODAY_DAY))" -gt "$CATCH_UP_DAYS" ]; then
    continue   # silent - true for ~25 of 30 days by design, same as sweep_and_upload.sh's gates
  fi

  # The month that just ended, computed by plain arithmetic on today's own year/month rather than
  # a `date -d "last month"`-style relative parse - Termux's `date` isn't guaranteed to be GNU
  # coreutils (may be a more minimal toybox/busybox build depending on what's installed), and this
  # project already prefers small arithmetic it can be sure works over an unverified date-parsing
  # extension. Correct for any day within CATCH_UP_DAYS, not just day 1, since it only depends on
  # today's own month/year, not "yesterday".
  TODAY_YEAR="$(TZ="$ZONE" date +%Y)"
  TODAY_MONTH="$(TZ="$ZONE" date +%m)"
  if [ "$((10#$TODAY_MONTH))" -eq 1 ]; then
    LAST_YEAR=$((TODAY_YEAR - 1))
    LAST_MONTH_NUM=12
  else
    LAST_YEAR="$TODAY_YEAR"
    LAST_MONTH_NUM=$((10#$TODAY_MONTH - 1))
  fi
  MONTH="$(printf "%s-%02d" "$LAST_YEAR" "$LAST_MONTH_NUM")"

  MARKER="$WORK_DIR/.archived_${CITY}_${MONTH}"
  [ -f "$MARKER" ] && continue   # already done this month, silent

  DIRS=()
  for D in "$WORK_DIR"/positions_"${CITY}"_"${MONTH}"-*_*; do
    [ -d "$D" ] || continue
    DIRS+=("$D")
  done

  if [ "${#DIRS[@]}" -eq 0 ]; then
    # Nothing recorded that month for this city (e.g. added mid-month) - mark done, nothing to
    # upload, no point re-checking every day for the rest of the catch-up window.
    touch "$MARKER"
    continue
  fi

  ARCHIVE_NAME="${CITY}_snapshots_${MONTH}.tar.xz"
  ARCHIVE_PATH="${WORK_DIR}/${ARCHIVE_NAME}"

  # -C "$WORK_DIR" + basenames only, so the archive contains plain
  # "positions_<city>_<date>_<time>/..." entries, not this phone's absolute home-directory path.
  BASENAMES=()
  for D in "${DIRS[@]}"; do
    BASENAMES+=("$(basename "$D")")
  done
  # Piped rather than tar's own `-J` shorthand, so xz's flags (multi-threaded `-6`, not `-9e` -
  # see this script's header comment for the on-device benchmark behind that choice) can be
  # controlled directly. `set -o pipefail` (top of this script) makes `$?` reflect either side's
  # failure, not just tar's.
  if ! tar -C "$WORK_DIR" -cf - "${BASENAMES[@]}" | xz -6 -T0 > "$ARCHIVE_PATH"; then
    echo "WARNING: tar/xz failed for ${CITY} ${MONTH} - will retry next run, source directories left untouched" >&2
    rm -f "$ARCHIVE_PATH"
    continue
  fi

  # Verify before ever touching the source directories - a corrupt archive must never be the
  # reason real recorded data gets deleted.
  if ! xz -t "$ARCHIVE_PATH"; then
    echo "WARNING: xz integrity check failed for ${ARCHIVE_PATH} - will retry next run, source directories left untouched" >&2
    rm -f "$ARCHIVE_PATH"
    continue
  fi

  LOCAL_SIZE=$(wc -c < "$ARCHIVE_PATH")
  TAG="raw-snapshots-${MONTH}"

  # Find or create this month's archive release - same idiom as sweep_and_upload.sh's per-day
  # raw-data release (GET the tag, POST a new release only if it doesn't exist yet). Whichever
  # city's run reaches this first for a given month creates the release; every other city that
  # month just uploads its own asset to the same one.
  RELEASE_JSON=$(curl -sS -H "Authorization: token ${GH_TOKEN}" \
    "${API}/repos/${REPO}/releases/tags/${TAG}")
  UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url // empty' | sed 's/{?name,label}//')

  if [ -z "$UPLOAD_URL" ]; then
    RELEASE_JSON=$(curl -sS -X POST -H "Authorization: token ${GH_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"tag_name\":\"${TAG}\",\"name\":\"Raw GPS snapshot recordings — ${MONTH}\",\"prerelease\":true,\"body\":\"Raw GTFS-RT VehiclePositions protobuf snapshots recorded during ${MONTH}, one solid tar+xz archive per city. Uploaded automatically by scripts/termux/archive_monthly.sh so this data doesn't have to stay on the recording phone indefinitely.\"}" \
      "${API}/repos/${REPO}/releases")
    UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url' | sed 's/{?name,label}//')
  fi

  if [ -z "$UPLOAD_URL" ] || [ "$UPLOAD_URL" = "null" ]; then
    echo "ERROR: could not find or create release ${TAG} for city ${CITY} - will retry next run, source directories left untouched" >&2
    rm -f "$ARCHIVE_PATH"
    continue
  fi

  # If an asset with this exact name is already on the release (an earlier run got as far as
  # uploading but crashed/lost connectivity before reaching cleanup below), don't re-upload -
  # GitHub's API rejects a duplicate asset name on the same release outright. Matching size means
  # treat it as done; a different size means a previous upload was cut short - replace it.
  EXISTING_ASSET=$(echo "$RELEASE_JSON" | jq -c --arg name "$ARCHIVE_NAME" '.assets[]? | select(.name == $name)')
  if [ -n "$EXISTING_ASSET" ]; then
    EXISTING_SIZE=$(echo "$EXISTING_ASSET" | jq -r '.size')
    if [ "$EXISTING_SIZE" = "$LOCAL_SIZE" ]; then
      echo "Asset ${ARCHIVE_NAME} already on ${TAG} with matching size - an earlier run must have uploaded it but not finished cleanup; skipping re-upload"
    else
      ASSET_ID=$(echo "$EXISTING_ASSET" | jq -r '.id')
      echo "WARNING: asset ${ARCHIVE_NAME} already exists on ${TAG} but with a different size (existing ${EXISTING_SIZE}, fresh ${LOCAL_SIZE}) - deleting the stale copy and re-uploading" >&2
      curl -sS -X DELETE -H "Authorization: token ${GH_TOKEN}" "${API}/repos/${REPO}/releases/assets/${ASSET_ID}" >/dev/null
      EXISTING_ASSET=""
    fi
  fi

  if [ -z "$EXISTING_ASSET" ]; then
    HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
      -H "Authorization: token ${GH_TOKEN}" \
      -H "Content-Type: application/x-xz" \
      --data-binary "@${ARCHIVE_PATH}" \
      "${UPLOAD_URL}?name=${ARCHIVE_NAME}")
    if [ "$HTTP_CODE" != "201" ]; then
      echo "WARNING: upload of ${ARCHIVE_NAME} failed (HTTP ${HTTP_CODE}) - will retry next run, source directories left untouched" >&2
      rm -f "$ARCHIVE_PATH"
      continue
    fi
  fi

  rm -f "$ARCHIVE_PATH"

  for D in "${DIRS[@]}"; do
    rm -rf "$D" "${D}.uploaded"
  done
  touch "$MARKER"
  echo "Archived ${CITY} ${MONTH}: ${#DIRS[@]} director$( [ "${#DIRS[@]}" -eq 1 ] && echo y || echo ies ) -> ${ARCHIVE_NAME} (${LOCAL_SIZE} bytes)"
done
