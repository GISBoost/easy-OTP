# verify_departure_date.R -- standalone, dependency-free sanity check for the
# GTFS_DATE -> departure weekday logic used by compute_isochrones_city.R.
# Catches the class of bug fixed 2026-08-31: a hardcoded departure date that
# doesn't match the actual GTFS release's service calendar. isochrone(mode =
# c("WALK","TRANSIT")) doesn't error on this -- it silently degrades to
# walk-only results for every origin/hour (confirmed live on GZM, whose
# release only has service_id active on 2026-08-28, while the departure date
# was hardcoded to 2026-08-24). Base R only (no r5r/sf/data.table), so it
# runs without the full R5R toolchain -- a fast pre-check before spending CI
# budget on a compute that would silently produce walk-only results.
#
# Usage: Rscript verify_departure_date.R <path-to-gtfs.zip> <GTFS_DATE ISO e.g. 2026-08-28>
# Exit 0 + PASS if >=1 GTFS service_id is active on that date, exit 1 + FAIL otherwise.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("usage: Rscript verify_departure_date.R <gtfs.zip> <GTFS_DATE YYYY-MM-DD>")
gtfs_zip <- args[1]
gtfs_date <- args[2]

departure_date <- as.Date(gtfs_date)
date_num <- as.integer(format(departure_date, "%Y%m%d"))
# weekdays() is locale-dependent (returns "piatek" not "friday" under a
# Polish locale) -- $wday is always 0=Sunday..6=Saturday regardless of
# locale, so index into a fixed English name table instead.
weekday_names <- c("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
weekday_col <- weekday_names[as.POSIXlt(departure_date)$wday + 1]  # matches GTFS calendar.txt column names

tmp <- tempfile()
dir.create(tmp)
on.exit(unlink(tmp, recursive = TRUE))
unzip(gtfs_zip, files = c("calendar.txt", "calendar_dates.txt"), exdir = tmp, junkpaths = TRUE)

active <- character(0)

cal_path <- file.path(tmp, "calendar.txt")
if (file.exists(cal_path)) {
  cal <- read.csv(cal_path, colClasses = "character", fileEncoding = "UTF-8-BOM")
  names(cal) <- tolower(names(cal))
  in_range <- as.integer(cal$start_date) <= date_num & date_num <= as.integer(cal$end_date)
  active <- c(active, cal$service_id[in_range & cal[[weekday_col]] == "1"])
}

cd_path <- file.path(tmp, "calendar_dates.txt")
if (file.exists(cd_path)) {
  cd <- read.csv(cd_path, colClasses = "character", fileEncoding = "UTF-8-BOM")
  names(cd) <- tolower(names(cd))
  added <- cd$service_id[as.integer(cd$date) == date_num & cd$exception_type == "1"]
  removed <- cd$service_id[as.integer(cd$date) == date_num & cd$exception_type == "2"]
  active <- union(setdiff(active, removed), added)
}

active <- unique(active)
if (length(active) == 0) {
  cat(sprintf("FAIL: %s -> %s (%d): 0 active service_id in %s\n", gtfs_date, weekday_col, date_num, gtfs_zip))
  quit(status = 1)
} else {
  cat(sprintf("PASS: %s -> %s (%d): %d active service_id(s) in %s\n", gtfs_date, weekday_col, date_num, length(active), gtfs_zip))
  quit(status = 0)
}
