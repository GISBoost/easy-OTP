# Dry run for the multi-city pipeline (compute_isochrones_city.R): measure
# r5r::isochrone() cost per origin on a REAL built network, before committing
# a multi-hour CI job to the full 17-hour sweep. dry_run_isochrone.R is the
# Lodz-only original (hardcoded paths/grid); this generalizes it the same way
# compute_isochrones_city.R generalized compute_isochrones.R.
#
# Why this matters more here than it did for the first 5 cities: R5's
# per-call memory/time scales with origins x network complexity, not just
# origins (see compute_isochrones_city.R header) -- Warszawa's two failed CI
# runs (OOM, then an isoband bug) both cost ~4h+ before failing. A city whose
# network is far more complex than its origin count alone suggests (GZM:
# many operators across 41 municipalities) needs this measured, not assumed.
#
# Usage: Rscript dry_run_isochrone_city.R <city> <variant: static|rt> [n_sample_origins=15]

args <- commandArgs(trailingOnly = TRUE)
city <- args[1]
variant <- args[2]
n_sample <- as.integer(ifelse(length(args) >= 3, args[3], 15))

xmx <- Sys.getenv("R5R_JAVA_XMX", "8G")
options(java.parameters = paste0("-Xmx", xmx))  # MUST precede library(r5r)

lib <- Sys.getenv("R_LIBS_USER")
if (nzchar(lib)) .libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

data_path <- sprintf("%s_network_%s", city, variant)
r5r_core <- setup_r5(data_path = data_path, verbose = FALSE)

origins_file <- switch(city,
  warszawa = "warszawa_hex_origins_1000m.csv",
  gzm = "gzm_hex_origins_1000m.csv",
  sprintf("%s_hex_origins.csv", city)
)
origins_all <- fread(
  file.path("..", "accessibility_cities", city, origins_file),
  colClasses = list(character = "id")
)
cat(sprintf("full origin grid: %d points\n", nrow(origins_all)))

idx <- round(seq(1, nrow(origins_all), length.out = n_sample))
sample_origins <- origins_all[idx]
cat(sprintf("dry-run sample: %d origins (systematic spread across full grid)\n", nrow(sample_origins)))

departure_dt <- as.POSIXct("24-08-2026 08:00:00", format = "%d-%m-%Y %H:%M:%S")

start <- Sys.time()
iso <- isochrone(
  r5r_core,
  origins = sample_origins,
  mode = c("WALK", "TRANSIT"),
  cutoffs = c(15, 30, 45),
  departure_datetime = departure_dt,
  polygon_output = TRUE,
  # Provably lossless, large speedup on complex networks -- see
  # compute_isochrones_city.R for rationale. Dry run must match the real
  # compute script's settings or this measurement isn't representative.
  max_walk_time = 45,
  progress = TRUE
)
elapsed <- as.numeric(difftime(Sys.time(), start, units = "secs"))

cat(sprintf("\nelapsed: %.1f s for %d origins (%.3f s/origin)\n",
            elapsed, nrow(sample_origins), elapsed / nrow(sample_origins)))
cat(sprintf("rows returned: %d (expected %d origins x 3 cutoffs = %d)\n",
            nrow(iso), nrow(sample_origins), nrow(sample_origins) * 3))

HOURS <- 17L  # 06:00..22:00, matches compute_isochrones_city.R
full_n <- nrow(origins_all)
per_origin_sec <- elapsed / nrow(sample_origins)
full_sec <- per_origin_sec * full_n * HOURS
cat(sprintf("\n--- extrapolation for FULL %s/%s sweep (%d origins x %d hourly steps) ---\n",
            city, variant, full_n, HOURS))
cat(sprintf("estimated time (this machine, no batching overhead): %.1f min (%.2f h)\n",
            full_sec / 60, full_sec / 3600))
cat(sprintf("GitHub Actions ubuntu-latest is 4-core vs. this machine -- expect real CI wall\n"))
cat(sprintf("time to be higher than this raw extrapolation, not lower.\n"))
cat(sprintf("CI hard cap: 340 min job timeout (360 min platform ceiling).\n"))

stop_r5(r5r_core)
