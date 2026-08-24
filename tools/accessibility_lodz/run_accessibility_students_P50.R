# P50 baseline for the Method C (P85) comparison: identical params to
# run_accessibility_students_C_p85.R but on the cached P50 network, so the two
# runs differ ONLY in which GTFS (median vs 85th-percentile reconstructed
# schedule) was used -- isolating the day-to-day reliability effect, same idea
# as Braga et al. (2026)'s P50 vs P85 comparison for Fortaleza.
#
# Usage: Rscript run_accessibility_students_P50.R

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

options(java.parameters = "-Xmx4G")

# P50 (median/"typical day") network -- reused from disk cache.
r5r_core <- setup_r5(data_path = "network_data", verbose = FALSE)

origins <- fread("lodz_hex_origins.csv", colClasses = list(character = "id"))
destinations <- fread("lodz_uni_destinations.csv", colClasses = list(character = "id"))

# Single-percentile (default 50 = median), 2h peak window -- deliberately the
# SAME params as run_accessibility_students_C_p85.R except for which network
# is loaded, so the only thing that can differ between the two output CSVs is
# the P50-vs-P85 GTFS itself.
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

fwrite(acc, "lodz_students_accessibility_P50.csv")
cat(sprintf("wrote %d rows to lodz_students_accessibility_P50.csv\n", nrow(acc)))

stop_r5(r5r_core)
