#!/usr/bin/env bash
set -uo pipefail   # not -e: one failed directory shouldn't stop the others

# Daily sweep of unuploaded recording directories for the easy-GTFS-RT Termux
# phone-recording experiment (TX-3). Zips and uploads each of today's
# positions_<date>_* directories (produced by record_supervised.sh, TX-2) as an
# asset of a positions-raw-<date> pre-release in GISBoost/easy-GTFS-RT. If at
# least one directory actually uploaded, fires a repository_dispatch event so
# the build workflow starts within seconds instead of waiting for its 22:15
# schedule (TX-7).
# Meant to run daily at 22:10 Europe/Warsaw via cronie - see the crontab entry in
# docs/prompts/termux-migration_prompts_for-claude-code.md, Prompt TX-3.
# Safe to re-run manually for testing: the .uploaded marker prevents duplicate
# uploads of directories already sent.

source "$HOME/.easy-gtfs-rt-termux.env"
REPO="GISBoost/easy-GTFS-RT"
DATE="$(TZ=Europe/Warsaw date +%F)"
TAG="positions-raw-${DATE}"
API="https://api.github.com"
WORK_DIR="$HOME/easy-gtfs-rt-termux"

# Find or create today's raw-data release.
RELEASE_JSON=$(curl -sS -H "Authorization: token ${GH_TOKEN}" \
  "${API}/repos/${REPO}/releases/tags/${TAG}")
UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url // empty' | sed 's/{?name,label}//')

if [ -z "$UPLOAD_URL" ]; then
  RELEASE_JSON=$(curl -sS -X POST -H "Authorization: token ${GH_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"tag_name\":\"${TAG}\",\"name\":\"Raw recordings ${DATE} (Termux)\",\"prerelease\":true,\"body\":\"Raw positions from the phone (TX-2/TX-3). Consumed by TX-4.\"}" \
    "${API}/repos/${REPO}/releases")
  UPLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.upload_url' | sed 's/{?name,label}//')
fi

if [ -z "$UPLOAD_URL" ] || [ "$UPLOAD_URL" = "null" ]; then
  echo "ERROR: could not find or create release ${TAG}" >&2
  exit 1
fi

UPLOADED_THIS_RUN=0

for DIR in "$WORK_DIR"/positions_"${DATE}"_*; do
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

# Only fire the build trigger if something new actually uploaded this run - avoids starting a
# build attempt (and its failure-path WhatsApp message) on a day with nothing new to build, e.g.
# a re-run where everything was already marked .uploaded. event_type here must match the workflow
# file's `on.repository_dispatch.types` exactly (case-sensitive) - see that file's comment.
if [ "$UPLOADED_THIS_RUN" -gt 0 ]; then
  DISPATCH_CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: token ${GH_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "Content-Type: application/json" \
    -d "{\"event_type\":\"phone-sweep-complete\",\"client_payload\":{\"date\":\"${DATE}\"}}" \
    "${API}/repos/${REPO}/dispatches")

  if [ "$DISPATCH_CODE" = "204" ]; then
    echo "Dispatched build for ${DATE} (${UPLOADED_THIS_RUN} upload$( [ "$UPLOADED_THIS_RUN" -eq 1 ] && echo "" || echo "s" ) this run)"
  else
    echo "WARNING: repository_dispatch failed (HTTP ${DISPATCH_CODE}) - build won't start immediately; the 22:15 schedule fallback (or TX-6's healthcheck) will still catch this" >&2
  fi
else
  echo "No new uploads this sweep - skipping repository_dispatch"
fi
