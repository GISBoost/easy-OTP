# Dry run: measure r5r::isochrone() cost per origin, to decide whether the
# full sweep (all 250m-grid origins x 65 time-of-day steps x 2 GTFS variants)
# is feasible before committing hours of compute to it.
#
# Usage: Rscript dry_run_isochrone.R <variant: static|rt> <n_sample_origins>

args <- commandArgs(trailingOnly = TRUE)
variant <- args[1]
n_sample <- as.integer(ifelse(length(args) >= 2, args[2], 15))
sample_size_param <- as.numeric(ifelse(length(args) >= 3, args[3], 0.8))

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(data.table)

options(java.parameters = "-Xmx4G")

data_path <- paste0("network_", variant)
r5r_core <- setup_r5(data_path = data_path, verbose = FALSE)

origins_all <- fread("lodz_hex250_origins.csv", colClasses = list(character = "id"))
cat(sprintf("full origin grid: %d points\n", nrow(origins_all)))

# systematic spread across the grid rather than a random cluster
idx <- round(seq(1, nrow(origins_all), length.out = n_sample))
sample_origins <- origins_all[idx]
cat(sprintf("dry-run sample: %d origins\n", nrow(sample_origins)))

departure_dt <- as.POSIXct("21-08-2026 08:00:00", format = "%d-%m-%Y %H:%M:%S")

start <- Sys.time()
iso <- isochrone(
  r5r_core,
  origins = sample_origins,
  mode = c("WALK", "TRANSIT"),
  cutoffs = c(15, 30, 45),
  departure_datetime = departure_dt,
  polygon_output = TRUE,
  sample_size = sample_size_param,
  # Provably lossless, large speedup on complex networks -- see
  # compute_isochrones_city.R for rationale.
  max_walk_time = 45,
  progress = TRUE
)
elapsed <- as.numeric(difftime(Sys.time(), start, units = "secs"))

cat(sprintf("\nelapsed: %.1f s for %d origins (%.2f s/origin)\n",
            elapsed, nrow(sample_origins), elapsed / nrow(sample_origins)))
cat(sprintf("rows returned: %d (expected %d origins x 3 cutoffs = %d)\n",
            nrow(iso), nrow(sample_origins), nrow(sample_origins) * 3))

out_path <- sprintf("dry_run_%s.gpkg", variant)
sf::st_write(iso, out_path, layer = "isochrones", delete_dsn = TRUE, quiet = TRUE)
file_size_mb <- file.size(out_path) / 1e6
cat(sprintf("wrote %s (%.2f MB) -> %.3f MB/origin\n", out_path, file_size_mb,
            file_size_mb / nrow(sample_origins)))

# extrapolate full sweep: all origins x 65 time steps x 2 variants
full_n <- nrow(origins_all)
per_origin_sec <- elapsed / nrow(sample_origins)
full_sec <- per_origin_sec * full_n * 65 * 2
full_mb <- (file_size_mb / nrow(sample_origins)) * full_n * 65 * 2
cat(sprintf("\n--- extrapolation for FULL sweep (%d origins x 65 steps x 2 variants) ---\n", full_n))
cat(sprintf("estimated time: %.1f min (%.1f h)\n", full_sec / 60, full_sec / 3600))
cat(sprintf("estimated raw output size (before simplification/compression): %.0f MB\n", full_mb))

stop_r5(r5r_core)
