# Generalized r5r accessibility run for the multi-city pipeline: one city, one
# destination set (services or universities), P50 realized GTFS, single median
# percentile over the 07:00-09:00 morning-peak window -- same params as
# accessibility_lodz's run_accessibility.R/run_accessibility_hex.R, just
# parameterized instead of hardcoded per script. No Method A/C here (scoped
# out for the multi-city pass per Michal's instruction -- see RESEARCH_LOG.md
# "Etap 5" for why).
#
# Usage: Rscript run_accessibility.R <city> <kind> <departure_date>
#   kind: "service" or "uni" (matches the destinations/opportunity file naming)
#   departure_date: e.g. "22-08-2026" (the realized GTFS's recorded service day)

args <- commandArgs(trailingOnly = TRUE)
city <- args[1]
kind <- args[2]              # "service" or "uni"
departure_date <- args[3]

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

options(java.parameters = "-Xmx4G")

# setup_r5 scans data_path for exactly the .pbf/.zip it needs and ignores
# everything else (CSVs, gpkg, etc.) -- pointing it straight at the city
# folder avoids duplicating the (tens-of-MB) pbf/GTFS into a separate
# network_data/ subfolder like accessibility_lodz did.
data_path <- city
r5r_core <- setup_r5(data_path = data_path, verbose = FALSE)

origins <- fread(file.path(city, paste0(city, "_hex_origins.csv")), colClasses = list(character = "id"))
dest_file <- file.path(city, paste0(city, "_", kind, "_destinations.csv"))
destinations <- fread(dest_file, colClasses = list(character = "id"))

# opportunity columns are opp0..opp{n-1} + total (see prepare_destinations.py's
# slug scheme -- avoids relying on Polish-diacritic column names in R/Java)
opp_cols <- setdiff(names(destinations), c("id", "lon", "lat"))

departure_dt <- as.POSIXct(paste(departure_date, "07:00:00"), format = "%d-%m-%Y %H:%M:%S")

acc <- accessibility(
  r5r_core,
  origins = origins,
  destinations = destinations,
  opportunities_colnames = opp_cols,
  mode = c("WALK", "TRANSIT"),
  departure_datetime = departure_dt,
  time_window = 120,
  max_trip_duration = 90,
  decay_function = "step",
  cutoffs = c(15, 30, 45, 60),
  progress = TRUE
)

out_file <- file.path(city, paste0(city, "_", kind, "_accessibility.csv"))
fwrite(acc, out_file)
cat(sprintf("wrote %d rows to %s\n", nrow(acc), out_file))

stop_r5(r5r_core)
