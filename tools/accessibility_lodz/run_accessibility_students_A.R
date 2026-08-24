# Method A: widen the departure window to the whole recorded service day
# (06:00-22:00, 960 min) and request multiple percentiles, instead of a single
# median over a 2h peak window. Gives a distributional view of accessibility
# across the day (typical vs good vs bad moments) at each origin, which can
# then be mapped spatially (does variability-across-the-day cluster somewhere?).
#
# Target group: population aged 20-29 (student-age proxy) per 500m hex,
# destinations: academic buildings of PL/UL/UM (Lodz's 3 largest universities).
# Reuses the cached P50 network (network_data/network.dat) -- no rebuild.
#
# Usage: Rscript run_accessibility_students_A.R

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

options(java.parameters = "-Xmx4G")

# Same P50 network as run_accessibility.R/run_accessibility_hex.R -- reused
# from disk cache, not rebuilt.
r5r_core <- setup_r5(data_path = "network_data", verbose = FALSE)

# Origins: hex centroids (same file as the hex service-accessibility run).
# Destinations: the 47 OSM university-building points (prepare_uni_destinations.py),
# not the 1328 service POIs -- this is a different target group/opportunity set.
origins <- fread("lodz_hex_origins.csv", colClasses = list(character = "id"))
destinations <- fread("lodz_uni_destinations.csv", colClasses = list(character = "id"))

# time_window=960 spans the whole recorded service day (06:00-22:00), so R5
# evaluates one travel time per origin-destination pair for EACH of 960
# departure minutes (vs. 120 in the peak-only runs). percentiles requests 5
# points of the resulting distribution instead of just the median (R5 caps
# this at 5 percentiles per call) -- p5 = accessibility under the FASTEST 5%
# of departures (best case), p95 = under the slowest 5% (worst case). Do not
# assume p95 > p5 numerically: p95 is a percentile of TRAVEL TIME, so it maps
# to LOWER accessibility (fewer opportunities reachable), and p5 to higher --
# see analyze_students_methods.py's spread calculation (p5 - p95, not p95 - p5).
acc <- accessibility(
  r5r_core,
  origins = origins,
  destinations = destinations,
  opportunities_colnames = c("politechnika", "uniwersytet", "medyczny", "total"),
  mode = c("WALK", "TRANSIT"),
  departure_datetime = as.POSIXct("21-08-2026 06:00:00", format = "%d-%m-%Y %H:%M:%S"),
  time_window = 960,
  percentiles = c(5, 25, 50, 75, 95),
  max_trip_duration = 90,
  decay_function = "step",
  cutoffs = c(15, 30, 45, 60),
  progress = TRUE
)

fwrite(acc, "lodz_students_accessibility_A_percentiles.csv")
cat(sprintf("wrote %d rows to lodz_students_accessibility_A_percentiles.csv\n", nrow(acc)))

stop_r5(r5r_core)
