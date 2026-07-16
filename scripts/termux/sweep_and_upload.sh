#!/usr/bin/env bash
set -uo pipefail   # not -e: one failed city/directory shouldn't stop the others

# Sweep of unuploaded recording directories for the easy-GTFS-RT Termux phone-recording
# experiment (TX-3). For every configured city (one ~/easy-gtfs-rt-termux/cities/<city>.env file
# per city, TX-8), zips and uploads that city's today's positions_<city>_<date>_* directories
# (produced by record_supervised.sh, TX-2) as an asset of a positions-raw-<city>-<date>
# pre-release in GISBoost/easy-GTFS-RT. If a city had at least one directory actually upload (this
# run or an earlier one still awaiting a successful dispatch - see DISPATCH_MARKER below), fires a
# repository_dispatch event carrying that city's id so the build workflow starts within seconds.
# Each city is independent - one city's failure (zip, upload, or dispatch) does not stop the
# others from being swept.
#
# Meant to run every 15 minutes, all day, via cronie - see the crontab entry in
# docs/prompts/termux-migration_prompts_for-claude-code.md, Prompt TX-3. This used to be a single
# daily trigger at 22:10 Europe/Warsaw, which was only safe because every city shared Poland's
# offset; since record_supervised.sh's recording window became per-city (TIMEZONE in
# cities/<city_id>.env), a city behind Warsaw (e.g. Lisbon, UTC-1) could still be actively
# recording when a fixed Warsaw-time sweep fired, truncating that day's session forever (the
# .uploaded marker never lets a swept directory be reconsidered). Two gates below make frequent,
# schedule-agnostic polling safe and cheap instead - see each gate's own comment. This also means
# this script needs no manual DST-flip twice a year (unlike the GitHub Actions crons this
# pipeline also uses) - TZ="$ZONE" date already knows about each city's own DST rules.
#
# DISPATCH_MARKER (added alongside dropping the build workflow's schedule fallback, see that
# workflow's own header comment): upload and dispatch are two separate curl calls, so a transient
# network blip between them could previously succeed at uploading but never announce it - once
# every directory for that date was .uploaded, Gate 2 skipped the city outright, dispatch
# included, with no future tick ever retrying. A per-city-per-date .dispatched_<city>_<date>
# marker (independent of the per-directory .uploaded markers) now lets a failed dispatch be
# retried on every subsequent tick until it actually succeeds, same reliability class as the
# upload retries already had.
#
# Safe to re-run manually for testing: the .uploaded marker prevents duplicate
# uploads of directories already sent, and the .dispatched marker prevents duplicate dispatches.
#
# Usage: sweep_and_upload.sh [YYYY-MM-DD]  (optional - defaults to each city's own local "today",
# per its cities/<city_id>.env TIMEZONE - see record_supervised.sh). When given, the override
# applies to every city uniformly (same calendar date string, not re-localized per city) - it's
# for manual recovery of a past day (e.g. re-sweeping after directories were renamed to match a
# naming-scheme change), and also bypasses Gate 1 below (see its comment) - the cron invocation
# never passes it.

source "$HOME/.easy-gtfs-rt-termux.env"
REPO="GISBoost/easy-GTFS-RT"
DATE_OVERRIDE="${1:-}"
API="https://api.github.com"
WORK_DIR="$HOME/easy-gtfs-rt-termux"

# Mirrors record_supervised.sh's own WINDOW_END (22:00) plus the same 10-minute safety buffer the
# original single-timezone design always had between window-close and sweep (time for the
# recording process to exit and flush recording.json before anything zips its directory). Kept as
# a separate literal here (not shared/sourced from record_supervised.sh) - small, self-contained
# scripts duplicating a handful of lines is this project's established convention (see e.g.
# family_a/recorder.py's own "intentional duplicate" docstring); keep both in sync by hand if
# WINDOW_END ever changes.
WINDOW_END_MIN=$(( 22 * 60 ))
SAFETY_MARGIN_MIN=10

for CITY_ENV in "$WORK_DIR"/cities/*.env; do
  [ -f "$CITY_ENV" ] || continue
  CITY="$(basename "$CITY_ENV" .env)"

  # Reset before sourcing so a city whose .env doesn't set TIMEZONE doesn't inherit whatever an
  # earlier city in this same loop left behind. Falls back to Europe/Warsaw, matching
  # record_supervised.sh's default - directories for a city with no TIMEZONE set are still dated
  # in Warsaw time, consistent with what actually recorded them.
  unset TIMEZONE
  source "$CITY_ENV"
  ZONE="${TIMEZONE:-Europe/Warsaw}"
  DATE="${DATE_OVERRIDE:-$(TZ="$ZONE" date +%F)}"

  # Gate 1 (correctness): don't touch this city's directories until its OWN local recording
  # window has actually closed (+ the safety margin above). Without this, running every 15
  # minutes (instead of once a day) would grab a directory record_supervised.sh's family_a.cli
  # record subprocess is still actively writing into, truncating that day's session - see this
  # file's header comment. DATE_OVERRIDE (manual recovery of a past day) bypasses this: the
  # operator is explicitly asserting that day is already over.
  if [ -z "$DATE_OVERRIDE" ]; then
    # 10# forces base-10, same reason as record_supervised.sh's identical trick: date +%H/%M
    # zero-pads (e.g. "08"), which bash arithmetic would otherwise parse as invalid octal.
    NOW_MIN_LOCAL=$(( 10#$(TZ="$ZONE" date +%H) * 60 + 10#$(TZ="$ZONE" date +%M) ))
    if [ "$NOW_MIN_LOCAL" -lt "$(( WINDOW_END_MIN + SAFETY_MARGIN_MIN ))" ]; then
      continue   # silent - true for ~85-90% of ticks/day by design; not worth logging
    fi
  fi

  # Gate 2 (avoid pointless GitHub API traffic - Michal's ask): only talk to GitHub at all if
  # there's actually something un-uploaded to send, OR something already uploaded that still
  # needs its dispatch (re)tried (DISPATCH_MARKER, see header comment). A fully-swept AND
  # fully-dispatched city keeps passing Gate 1 for the rest of the day - without this check,
  # that's still one releases/tags GET per tick, all day, for nothing. Applies even under
  # DATE_OVERRIDE - if a manual recovery run finds nothing pending and nothing left to dispatch,
  # there's nothing to recover.
  DISPATCH_MARKER="$WORK_DIR/.dispatched_${CITY}_${DATE}"
  PENDING=false
  ANY_UPLOADED=false
  for DIR in "$WORK_DIR"/positions_"${CITY}"_"${DATE}"_*; do
    [ -d "$DIR" ] || continue
    if [ -f "${DIR}.uploaded" ]; then
      ANY_UPLOADED=true
    else
      PENDING=true
    fi
  done
  if [ "$PENDING" = false ] && { [ -f "$DISPATCH_MARKER" ] || [ "$ANY_UPLOADED" = false ]; }; then
    continue   # silent, same reasoning as Gate 1
  fi

  TAG="positions-raw-${CITY}-${DATE}"

  # Find or create today's raw-data release for this city.
  RELEASE_JSON=$(curl -sS -H "Authorization: token ${GH_TOKEN}" \
    "${API}/repos/${REPO}/releases/tags/${TAG}")
  UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url // empty' | sed 's/{?name,label}//')

  if [ -z "$UPLOAD_URL" ]; then
    RELEASE_JSON=$(curl -sS -X POST -H "Authorization: token ${GH_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"tag_name\":\"${TAG}\",\"name\":\"Raw recordings ${CITY} ${DATE} (Termux)\",\"prerelease\":true,\"body\":\"Raw positions from the phone (TX-2/TX-3). Consumed by TX-4.\"}" \
      "${API}/repos/${REPO}/releases")
    UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url' | sed 's/{?name,label}//')
  fi

  if [ -z "$UPLOAD_URL" ] || [ "$UPLOAD_URL" = "null" ]; then
    echo "ERROR: could not find or create release ${TAG} for city ${CITY} - skipping this city this sweep" >&2
    continue
  fi

  UPLOADED_THIS_RUN=0

  for DIR in "$WORK_DIR"/positions_"${CITY}"_"${DATE}"_*; do
    [ -d "$DIR" ] || continue
    [ -f "${DIR}.uploaded" ] && continue

    ZIP_NAME="$(basename "$DIR").zip"
    ZIP_PATH="${WORK_DIR}/${ZIP_NAME}"
    if ! (cd "$WORK_DIR" && zip -rq "$ZIP_NAME" "$(basename "$DIR")"); then
      echo "WARNING: zip creation failed for ${ZIP_NAME} - will retry next sweep" >&2
      rm -f "$ZIP_PATH"
      continue
    fi

    HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
      -H "Authorization: token ${GH_TOKEN}" \
      -H "Content-Type: application/zip" \
      --data-binary "@${ZIP_PATH}" \
      "${UPLOAD_URL}?name=${ZIP_NAME}")

    if [ "$HTTP_CODE" = "201" ]; then
      touch "${DIR}.uploaded"
      echo "Uploaded ${ZIP_NAME}"
      UPLOADED_THIS_RUN=$((UPLOADED_THIS_RUN + 1))
    else
      echo "WARNING: upload of ${ZIP_NAME} failed (HTTP ${HTTP_CODE}) - will retry next sweep" >&2
    fi
    rm -f "$ZIP_PATH"
  done

  # Fire the build trigger if something new actually uploaded this run for this city, OR if an
  # earlier run already uploaded everything but its own dispatch attempt never succeeded
  # (DISPATCH_MARKER absent - see header comment; there is no schedule-based fallback workflow
  # covering this anymore, so this retry loop is the only recovery path). event_type here must
  # match the workflow file's `on.repository_dispatch.types` exactly (case-sensitive), and `city`
  # must match a key in easy-GTFS-RT's config/cities.json exactly (case-sensitive too) - see that
  # file's comment.
  if [ "$UPLOADED_THIS_RUN" -gt 0 ] || { [ "$ANY_UPLOADED" = true ] && [ ! -f "$DISPATCH_MARKER" ]; }; then
    DISPATCH_CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
      -H "Authorization: token ${GH_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -H "Content-Type: application/json" \
      -d "{\"event_type\":\"phone-sweep-complete\",\"client_payload\":{\"city\":\"${CITY}\",\"date\":\"${DATE}\"}}" \
      "${API}/repos/${REPO}/dispatches")

    if [ "$DISPATCH_CODE" = "204" ]; then
      touch "$DISPATCH_MARKER"
      if [ "$UPLOADED_THIS_RUN" -gt 0 ]; then
        echo "Dispatched build for ${CITY} ${DATE} (${UPLOADED_THIS_RUN} upload$( [ "$UPLOADED_THIS_RUN" -eq 1 ] && echo "" || echo "s" ) this run)"
      else
        echo "Dispatched build for ${CITY} ${DATE} (retry - uploaded in an earlier run, dispatch had failed until now)"
      fi
    else
      echo "WARNING: repository_dispatch for ${CITY} failed (HTTP ${DISPATCH_CODE}) - will retry next sweep" >&2
    fi
  else
    echo "No new uploads this sweep for ${CITY} - skipping repository_dispatch"
  fi
done
