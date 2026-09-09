#!/usr/bin/env bash
set -uo pipefail   # not -e: a single failed day should just be retried next run

# TX-10 - once-a-day fetch of the mkuran.pl Polish national rail GTFS-RT TripUpdates
# aggregate (https://mkuran.pl/gtfs/polish_trains/updates.pb).
#
# This is the ONLY realtime source for Łódzka Kolej Aglomeracyjna (agency LKA) - ŁKA
# publishes no VehiclePositions feed at all. Background and the converter that consumes
# these archives:
#   easy-R5/docs/notes/realized-gtfs-lka-tripupdates.md
#   easy-OTP/tools/family_b_realized/  (build_realized.py)
#   easy-OTP/docs/reference/RT-3_realized-gtfs-notes.md
#
# Why a plain cron job, not a runit service like family-a-record-<city>:
#   - This feed is an AGGREGATE that already carries the whole previous service day
#     (plus today, still filling in). One fetch after midnight captures a complete,
#     settled day - there is nothing to gain from a minute-by-minute recording loop.
#   - It has ~2-day retention and no archive upstream, so the ONLY thing that matters
#     is that some fetch lands each day before the window closes. A GitHub Actions
#     backup (easy-GTFS-RT/.github/workflows/polish-trains-tripupdates-fetch.yml) writes
#     the SAME filename to the SAME release, so whichever runs first wins and the other
#     is a no-op.
#
# Self-contained: URL hardcoded (single feed, no cities/<id>.env), $GH_TOKEN from the
# shared ~/.easy-gtfs-rt-termux.env. Uploads via direct curl to the REST API (no `gh`
# on the phone), same idiom as sweep_and_upload.sh / archive_monthly.sh.
#
# Crontab entry (see scripts/termux/README.md "TX-10"):
#   30 3 * * * /data/data/com.termux/files/usr/bin/bash \
#     /data/data/com.termux/files/home/easy-gtfs-rt-termux/fetch_polish_trains_rt.sh \
#     >> /data/data/com.termux/files/home/easy-gtfs-rt-termux/logs/polish_trains_rt.log 2>&1

source "$HOME/.easy-gtfs-rt-termux.env"   # GH_TOKEN
REPO="GISBoost/easy-GTFS-RT"
API="https://api.github.com"
WORK_DIR="$HOME/easy-gtfs-rt-termux"
FEED_URL="https://mkuran.pl/gtfs/polish_trains/updates.pb"
MIN_BYTES=1000000   # a healthy fetch is ~6 MB raw / ~1.7 MB gzipped; guard against a truncated/empty response

mkdir -p "$WORK_DIR/polish_trains_rt" "$WORK_DIR/logs"

# The service day we're archiving = yesterday (today is still only partially run).
# python3 is provisioned by termux_provision.sh; Termux's `date` has no reliable
# relative-date support (toybox/busybox), same reason archive_monthly.sh does month
# math by hand.
DAY="$(python3 -c 'import datetime; print(datetime.date.today() - datetime.timedelta(days=1))')" || {
  echo "$(date -Iseconds) ERROR: could not compute yesterday's date" >&2
  exit 1
}
MONTH="${DAY%-*}"
TAG="polish-trains-tripupdates-raw-${MONTH}"
ASSET_NAME="polish_trains_updates_${DAY}.pb.gz"
OUT_PATH="${WORK_DIR}/polish_trains_rt/${ASSET_NAME}"

echo "$(date -Iseconds) fetching ${FEED_URL} for service day ${DAY}"

if ! curl -fsSL --max-time 180 "$FEED_URL" | gzip -9 > "$OUT_PATH"; then
  echo "$(date -Iseconds) WARNING: fetch/compress failed - will retry next run" >&2
  rm -f "$OUT_PATH"
  exit 1
fi

LOCAL_SIZE=$(wc -c < "$OUT_PATH")
if [ "$LOCAL_SIZE" -lt "$MIN_BYTES" ]; then
  echo "$(date -Iseconds) WARNING: fetched file only ${LOCAL_SIZE} bytes (< ${MIN_BYTES}) - discarding, will retry next run" >&2
  rm -f "$OUT_PATH"
  exit 1
fi

# Find or create this month's raw release - same GET-tag / POST-if-missing idiom as
# archive_monthly.sh.
RELEASE_JSON=$(curl -sS -H "Authorization: token ${GH_TOKEN}" \
  "${API}/repos/${REPO}/releases/tags/${TAG}")
UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url // empty' | sed 's/{?name,label}//')

if [ -z "$UPLOAD_URL" ]; then
  RELEASE_JSON=$(curl -sS -X POST -H "Authorization: token ${GH_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"tag_name\":\"${TAG}\",\"name\":\"Polish rail TripUpdates - raw ${MONTH}\",\"prerelease\":true,\"body\":\"Daily gzipped snapshots of https://mkuran.pl/gtfs/polish_trains/updates.pb (GTFS-RT TripUpdates, PKP PLK Otwarte Dane), one per settled service day. The only realtime source for Łódzka Kolej Aglomeracyjna. Consumed by easy-OTP/tools/family_b_realized/build_realized.py. Uploaded by scripts/termux/fetch_polish_trains_rt.sh (phone) or the polish-trains-tripupdates-fetch workflow (backup).\"}" \
    "${API}/repos/${REPO}/releases")
  UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url' | sed 's/{?name,label}//')
fi

if [ -z "$UPLOAD_URL" ] || [ "$UPLOAD_URL" = "null" ]; then
  echo "$(date -Iseconds) ERROR: could not find or create release ${TAG} - will retry next run" >&2
  rm -f "$OUT_PATH"
  exit 1
fi

# Already uploaded today (by an earlier run, or by the GitHub Actions backup)? Then stop.
# GitHub rejects a duplicate asset name on the same release anyway; treat any existing
# asset as done regardless of size (a same-day re-fetch of a settled feed is equivalent).
EXISTING=$(echo "$RELEASE_JSON" | jq -r --arg n "$ASSET_NAME" '.assets[]? | select(.name == $n) | .name')
if [ -n "$EXISTING" ]; then
  echo "$(date -Iseconds) ${ASSET_NAME} already on ${TAG} - nothing to do"
  rm -f "$OUT_PATH"
  exit 0
fi

HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
  -H "Authorization: token ${GH_TOKEN}" \
  -H "Content-Type: application/gzip" \
  --data-binary "@${OUT_PATH}" \
  "${UPLOAD_URL}?name=${ASSET_NAME}")

if [ "$HTTP_CODE" != "201" ]; then
  echo "$(date -Iseconds) WARNING: upload of ${ASSET_NAME} failed (HTTP ${HTTP_CODE}) - will retry next run" >&2
  rm -f "$OUT_PATH"
  exit 1
fi

rm -f "$OUT_PATH"
echo "$(date -Iseconds) uploaded ${ASSET_NAME} (${LOCAL_SIZE} bytes) to ${TAG}"
