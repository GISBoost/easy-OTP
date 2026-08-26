# Full isochrone sweep for one of the accessibility_cities SES-study cities
# (Warszawa, Krakow, Gdansk, Poznan, Szczecin) -- generalizes compute_isochrones.R
# (which stays Lodz-only: different origins file / departure date / folder
# names, not worth unifying for one extra branch).
#
# Both static and realized (GTFS-RT P50) variants are computed here, same as
# Lodz -- fetched fresh from the 2026-08-24 (Monday) easy-GTFS-RT release for
# each city, which bundles BOTH a realized_p50.zip AND a static_gtfs.zip for
# that exact day (see setup_city_networks.sh). This is a cleaner setup than
# tools/accessibility_cities' own SES-study runs, which used a Saturday
# recording (2026-08-22) with the departure date patched forward to Monday --
# workable for a single median-cutoff accessibility number, but here we want
# both variants genuinely describing the same service day, so we use the
# release that actually recorded that Monday.
#
# Same origins x hours x cutoffs budget as Lodz (500m hex grid, hourly
# 06:00-22:00, 15/30/45 min cutoffs) -- reuses each city's existing
# <city>_hex_origins.csv from the SES study (grid only, not GTFS-dependent).
#
# Usage: Rscript compute_isochrones_city.R <city> <variant: static|rt>
#   city: warszawa|krakow|gdansk|poznan|szczecin
#   env R5R_JAVA_XMX (default "8G"): JVM heap. MUST be set via options()
#   BEFORE library(r5r) -- r5r initializes its JVM at package load, not at
#   setup_r5()/isochrone() call time, so setting java.parameters afterwards is
#   silently ignored (confirmed live: an options() call placed after
#   library(r5r), same as the first version of this script and of
#   compute_isochrones.R, printed r5r's own "Currently, Java memory is set to
#   ." warning and ran with an unset/default heap).
#   env R5R_BATCH_SIZE (default 800): origins per isochrone() call. Warszawa
#   (2546 origins, the biggest network here) OOM'd ("java.lang.
#   OutOfMemoryError: Java heap space" inside FastRaptorWorker.
#   copyMultiRoundState) computing all origins in one batched call -- R5's
#   per-call RaptorState memory scales with origins x network complexity, not
#   just origins, so a bigger heap alone doesn't scale safely to every city.
#   Splitting into fixed-size batches bounds peak memory independent of a
#   city's total origin count (the Lodz dry run already found throughput is
#   good at 600+ origins/batch, so 800 keeps that while capping the risk).
# data_path is <city>_network_<variant>/ (built by setup_city_networks.sh --
# each variant needs its own copy of the .osm.pbf + exactly one GTFS zip,
# same reason Lodz has network_static/network_rt: static and realized GTFS
# reuse the same trip_id/stop_id, so they cannot share a data_path).

args <- commandArgs(trailingOnly = TRUE)
city <- args[1]
variant <- args[2]
valid_cities <- c("warszawa", "krakow", "gdansk", "poznan", "szczecin")
if (!city %in% valid_cities) {
  stop(sprintf("city must be one of: %s", paste(valid_cities, collapse = ", ")))
}
if (!variant %in% c("static", "rt")) stop("variant must be 'static' or 'rt'")

xmx <- Sys.getenv("R5R_JAVA_XMX", "8G")
options(java.parameters = paste0("-Xmx", xmx))  # MUST precede library(r5r) -- see header note

lib <- Sys.getenv("R_LIBS_USER")
if (nzchar(lib)) .libPaths(c(lib, .libPaths()))  # unset in CI, where packages already sit on the default path

library(r5r)
library(sf)

batch_size <- as.integer(Sys.getenv("R5R_BATCH_SIZE", "800"))

data_path <- sprintf("%s_network_%s", city, variant)
r5r_core <- setup_r5(data_path = data_path, verbose = FALSE)

origins <- data.table::fread(
  file.path("..", "accessibility_cities", city, sprintf("%s_hex_origins.csv", city)),
  colClasses = list(character = "id")
)
cat(sprintf("origins: %d (java heap %s, batch size %d)\n", nrow(origins), xmx, batch_size))

# Runs one hour's full sweep at a given batch size. Warszawa/rt 21:00 hit a
# real isoband bug here ("Found polygons without undefined interior/exterior
# relationship", inside isoband::iso_to_sfg -- a known edge case when the
# travel-time surface's contours are too fragmented/ambiguous to wind
# unambiguously into polygons) after ~4h10m of otherwise-clean compute, not a
# resource limit. It reproduces deterministically for that exact input, so a
# bare retry at the same batch size would just fail again -- retrying at a
# different batch size changes the surface's internal batching boundaries
# enough to route around it in practice.
run_hour <- function(bsize, departure_dt) {
  batch_starts <- seq(1, nrow(origins), by = bsize)
  batch_results <- vector("list", length(batch_starts))
  for (bi in seq_along(batch_starts)) {
    start <- batch_starts[bi]
    end <- min(start + bsize - 1, nrow(origins))
    batch_results[[bi]] <- isochrone(
      r5r_core,
      origins = origins[start:end, ],
      mode = c("WALK", "TRANSIT"),
      cutoffs = c(15, 30, 45),
      departure_datetime = departure_dt,
      polygon_output = TRUE,
      progress = FALSE
    )
  }
  do.call(rbind, batch_results)
}

hours <- 6:22  # 06:00 .. 22:00, 17 steps -- same budget as Lodz
results <- vector("list", length(hours))
skipped_hours <- integer(0)

for (i in seq_along(hours)) {
  h <- hours[i]
  departure_dt <- as.POSIXct(sprintf("24-08-2026 %02d:00:00", h), format = "%d-%m-%Y %H:%M:%S")
  t0 <- Sys.time()
  iso <- tryCatch(
    run_hour(batch_size, departure_dt),
    error = function(e) {
      half <- max(100L, batch_size %/% 2L)
      cat(sprintf("  [%2d/%2d] %02d:00 FAILED at batch_size=%d (%s) -- retrying at batch_size=%d\n",
                  i, length(hours), h, batch_size, conditionMessage(e), half))
      tryCatch(
        run_hour(half, departure_dt),
        error = function(e2) {
          cat(sprintf("  [%2d/%2d] %02d:00 FAILED AGAIN at batch_size=%d (%s) -- skipping this hour\n",
                      i, length(hours), h, half, conditionMessage(e2)))
          NULL
        }
      )
    }
  )
  if (is.null(iso)) {
    skipped_hours <- c(skipped_hours, h)
    next
  }
  iso$hour <- h
  results[[i]] <- iso
  elapsed <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  cat(sprintf("[%2d/%2d] %02d:00 -> %d features in %.1f s\n", i, length(hours), h, nrow(iso), elapsed))
}

results <- results[!vapply(results, is.null, logical(1))]
all_iso <- do.call(rbind, results)
cat(sprintf("\ntotal features: %d\n", nrow(all_iso)))
if (length(skipped_hours) > 0) {
  cat(sprintf("WARNING: %d hour(s) skipped after failing twice: %s\n",
              length(skipped_hours), paste(skipped_hours, collapse = ", ")))
}

out_path <- sprintf("%s_%s_isochrones_all.gpkg", city, variant)
sf::st_write(all_iso, out_path, layer = "isochrones", delete_dsn = TRUE, quiet = TRUE)
cat(sprintf("wrote %s (%.1f MB)\n", out_path, file.size(out_path) / 1e6))

stop_r5(r5r_core)
