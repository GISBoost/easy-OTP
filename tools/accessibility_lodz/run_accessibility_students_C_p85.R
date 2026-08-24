# Method C: day-to-day reliability comparison (Braga et al. 2026 style) -- rebuild
# the network on the P85 ("bad day"/dispersion) realized GTFS instead of P50
# (median/"typical day"), rerun the identical accessibility() call, and compare
# against the P50 result to see how much a bad-reliability day degrades student
# access to universities, and whether that degradation is spatially uneven.
#
# Separate data_path (network_data_p85/) -> separate R5 network build (not cached,
# ~1-2 min for a city this size). Same params otherwise as the P50 hex run.
#
# Usage: Rscript run_accessibility_students_C_p85.R

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

options(java.parameters = "-Xmx4G")

# network_data_p85/ has its OWN .osm.pbf + GTFS zip (the P85 realized feed) --
# a different data_path from the P50 runs means r5r cannot reuse network_data's
# cached network.dat, so this call does a full graph build (~20s for this city,
# see RESEARCH_LOG.md performance section), not an instant cache load.
r5r_core <- setup_r5(data_path = "network_data_p85", verbose = FALSE)

origins <- fread("lodz_hex_origins.csv", colClasses = list(character = "id"))
destinations <- fread("lodz_uni_destinations.csv", colClasses = list(character = "id"))

# Same params as run_accessibility_students_P50.R (see its comments) --
# origins/destinations/mode/window/cutoffs all identical, only the underlying
# GTFS differs (P85 dispersion schedule vs P50 median schedule).
acc <- accessibility(
  r5r_core,
  origins = origins,
  destinations = destinations,
  opportunities_colnames = c("politechnika", "uniwersytet", "medyczny", "total"),
  mode = c("WALK", "TRANSIT"),
  departure_datetime = as.POSIXct("21-08-2026 07:00:00", format = "%d-%m-%Y %H:%M:%S"),
  time_window = 120,
  max_trip_duration = 90,
  decay_function = "step",
  cutoffs = c(15, 30, 45, 60),
  progress = TRUE
)

fwrite(acc, "lodz_students_accessibility_C_p85.csv")
cat(sprintf("wrote %d rows to lodz_students_accessibility_C_p85.csv\n", nrow(acc)))

stop_r5(r5r_core)
