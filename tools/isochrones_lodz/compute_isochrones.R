# Full isochrone sweep for the izochrony-lodz web map: all 500m-grid origins,
# hourly departure times 06:00-22:00 (17 steps), cutoffs 15/30/45 min, for one
# GTFS variant (static or rt). Sized after dry_run_isochrone.R measured real
# r5r::isochrone() cost (~0.05 s/origin at this batch size) and the chosen
# origins x timesteps x variants budget (see plan / AskUserQuestion decision:
# 500m origins, hourly steps).
#
# Usage: Rscript compute_isochrones.R <variant: static|rt>
#
# NOTE (2026-08-26): options(java.parameters=...) must precede library(r5r) --
# r5r initializes its JVM at package load, not at setup_r5()/isochrone() call
# time, so setting it afterwards (as this script originally did) is silently
# ignored. Lodz's own 1479-origin runs already completed fine on whatever
# default heap that left in place, but the ordering was only proven unsafe
# later on Warszawa's bigger network (see compute_isochrones_city.R) -- fixed
# here too so a future re-run of this script doesn't inherit the same risk.

args <- commandArgs(trailingOnly = TRUE)
variant <- args[1]
if (!variant %in% c("static", "rt")) stop("variant must be 'static' or 'rt'")

xmx <- Sys.getenv("R5R_JAVA_XMX", "8G")
options(java.parameters = paste0("-Xmx", xmx))  # MUST precede library(r5r)

lib <- Sys.getenv("R_LIBS_USER")
.libPaths(c(lib, .libPaths()))

library(r5r)
library(sf)

data_path <- paste0("network_", variant)
r5r_core <- setup_r5(data_path = data_path, verbose = FALSE)

origins <- data.table::fread("lodz_origins_500.csv", colClasses = list(character = "id"))
cat(sprintf("origins: %d (java heap %s)\n", nrow(origins), xmx))

hours <- 6:22  # 06:00 .. 22:00, 17 steps
results <- vector("list", length(hours))

for (i in seq_along(hours)) {
  h <- hours[i]
  departure_dt <- as.POSIXct(sprintf("21-08-2026 %02d:00:00", h), format = "%d-%m-%Y %H:%M:%S")
  t0 <- Sys.time()
  iso <- isochrone(
    r5r_core,
    origins = origins,
    mode = c("WALK", "TRANSIT"),
    cutoffs = c(15, 30, 45),
    departure_datetime = departure_dt,
    polygon_output = TRUE,
    # Provably lossless (no walk leg > the largest cutoff can ever be part
    # of a trip within it) and a large speedup on complex networks -- see
    # compute_isochrones_city.R for the full rationale + GZM measurement.
    max_walk_time = 45,
    progress = FALSE
  )
  iso$hour <- h
  results[[i]] <- iso
  elapsed <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  cat(sprintf("[%2d/%2d] %02d:00 -> %d features in %.1f s\n", i, length(hours), h, nrow(iso), elapsed))
}

all_iso <- do.call(rbind, results)
cat(sprintf("\ntotal features: %d\n", nrow(all_iso)))

out_path <- sprintf("%s_isochrones_all.gpkg", variant)
sf::st_write(all_iso, out_path, layer = "isochrones", delete_dsn = TRUE, quiet = TRUE)
cat(sprintf("wrote %s (%.1f MB)\n", out_path, file.size(out_path) / 1e6))

stop_r5(r5r_core)
