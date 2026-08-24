# Static-GTFS baseline for the P50-vs-static audit: identical params to
# run_accessibility_students_P50.R, but network built from the STATIC GTFS
# (lodz_static_gtfs_2026-08-21.zip, same day, same source agency download used
# to build that day's realized/P50 feed) instead of the phone-corrected P50
# feed. Isolates whether the "56-70% of student hexes have zero university
# access" finding is a realized-GTFS artifact or holds under static too.
#
# Usage: Rscript run_accessibility_students_STATIC.R

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

options(java.parameters = "-Xmx4G")

r5r_core <- setup_r5(data_path = "network_data_static", verbose = FALSE)

origins <- fread("lodz_hex_origins.csv", colClasses = list(character = "id"))
destinations <- fread("lodz_uni_destinations.csv", colClasses = list(character = "id"))

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

fwrite(acc, "lodz_students_accessibility_STATIC.csv")
cat(sprintf("wrote %d rows to lodz_students_accessibility_STATIC.csv\n", nrow(acc)))

stop_r5(r5r_core)
