# Same accessibility() call as run_accessibility.R, but origins = 500m hex centroids
# (lodz_hex_origins.csv) instead of obwod-spisowy centroids -- uniform-size spatial
# units instead of tracts that range from tiny (dense center) to huge (city fringe).
# Reuses the cached network (network_data/network.dat) -- no rebuild.
#
# Usage: Rscript run_accessibility_hex.R

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

options(java.parameters = "-Xmx4G")

# Same data_path as run_accessibility.R -> setup_r5() finds network_data.dat
# already on disk and just loads it (seconds), no rebuild from the .pbf/.zip.
r5r_core <- setup_r5(data_path = "network_data", verbose = FALSE)

# Only the origins file changed (hex centroids instead of obwod centroids);
# destinations (the 1328 OSM service POIs) stay identical to run_accessibility.R,
# so results are directly comparable point-for-point.
origins <- fread("lodz_hex_origins.csv", colClasses = list(character = "id"))
destinations <- fread("lodz_destinations.csv", colClasses = list(character = "id"))

# Identical accessibility() call/params to run_accessibility.R -- see that
# script's comments for what each argument does.
acc <- accessibility(
  r5r_core,
  origins = origins,
  destinations = destinations,
  opportunities_colnames = c("education", "health", "culture", "groceries", "total"),
  mode = c("WALK", "TRANSIT"),
  departure_datetime = as.POSIXct("21-08-2026 07:00:00", format = "%d-%m-%Y %H:%M:%S"),
  time_window = 120,
  max_trip_duration = 90,
  decay_function = "step",
  cutoffs = c(15, 30, 45, 60),
  progress = TRUE
)

fwrite(acc, "lodz_hex_accessibility.csv")
cat(sprintf("wrote %d rows to lodz_hex_accessibility.csv\n", nrow(acc)))

stop_r5(r5r_core)
