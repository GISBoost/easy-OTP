# Transport accessibility for Lodz via r5r: multimodal (walk+transit) cumulative-
# opportunity accessibility from every obwod spisowy (census tract centroid) to
# public-service POIs (education/health/culture/groceries, from OSM), using the
# Family A "realized" GTFS for 2026-08-21 (actual observed service, not the
# published timetable) as the transit network.
#
# Usage: Rscript run_accessibility.R

# r5r needs its own R library path (system R library isn't writable without
# admin rights on this machine -- see HANDOFF.md) prepended ahead of the default.
lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

# JVM heap for the R5 routing engine (r5r runs R5 inside the R session's JVM
# via rJava) -- default is too small for a graph + accessibility run this size.
options(java.parameters = "-Xmx4G")

# Builds (or, if network_data/network.dat already exists, just loads) the R5
# multimodal graph from whatever .osm.pbf + GTFS .zip sit in data_path. First
# call for a given data_path is slow (graph build); every later call is fast
# (cached network.dat is reused, see HANDOFF.md gotcha about this).
r5r_core <- setup_r5(data_path = "network_data", verbose = FALSE)

# origins/destinations are plain CSVs with id/lon/lat (+ opportunity count
# columns on the destinations side) -- ids must stay character, not numeric,
# or r5r silently coerces/truncates large obwod-spisowy id values.
origins <- fread("lodz_origins.csv", colClasses = list(character = "id"))
destinations <- fread("lodz_destinations.csv", colClasses = list(character = "id"))

# Cumulative-opportunity accessibility: for each origin, count destinations of
# each opportunity type reachable within each cutoff. R5 evaluates travel time
# once per departure minute inside [departure_datetime, departure_datetime +
# time_window] (here: every minute from 07:00 to 09:00, 120 evaluations per
# origin) and reports the median (default percentile=50) accessibility across
# those evaluations -- i.e. "typical" access during the morning peak, not the
# best-case or a full-day cumulative fraction. See RESEARCH_LOG.md for the
# full explanation of this mechanic and how it differs from a day-long CDF.
acc <- accessibility(
  r5r_core,
  origins = origins,
  destinations = destinations,
  opportunities_colnames = c("education", "health", "culture", "groceries", "total"),
  mode = c("WALK", "TRANSIT"),
  departure_datetime = as.POSIXct("21-08-2026 07:00:00", format = "%d-%m-%Y %H:%M:%S"),
  time_window = 120,
  max_trip_duration = 90,
  decay_function = "step",  # hard threshold (count opportunities <= cutoff), not a decay curve
  cutoffs = c(15, 30, 45, 60),
  progress = TRUE
)

fwrite(acc, "lodz_accessibility.csv")
cat(sprintf("wrote %d rows to lodz_accessibility.csv\n", nrow(acc)))

# Releases the JVM-side R5 network object; without this the network stays
# loaded in memory for the rest of the R session (harmless here since the
# script exits right after, but good hygiene if reusing r5r_core interactively).
stop_r5(r5r_core)
