"""Stop-level headway, pooled across every line that serves the stop.

Different quantity from `tidy.headway_s`, not a derivation of it: that column is keyed on
`(route_id, direction_id, stop_id, stop_sequence)`, i.e. the gap between two vehicles of the
SAME line. A passenger standing at a physical stop does not care which line shows up next, so
this pools every crossing recorded at a `stop_id` - across all routes and trips - and diffs
consecutive arrival times.

`stop_id` already encodes the physical/directional position in GTFS (the two directions of a
street normally have distinct stop_ids), so unlike the per-line headway this needs no
`direction_id` filter to avoid mixing two different real-world platforms.

Requires a whole-feed tidy table (`extract` run without `--route`) - a route-filtered table
would silently pool only the lines that happened to be selected and understate every stop's
real frequency.

**`per_stop_summary` (the I37 map) and `citywide_hourly` (H31/J39) answer different
questions, on purpose, and their headline numbers will not match.** The map medians each
STOP's own headways over the whole day, then colours it in place - "how does this particular
spot behave". The chart medians every crossing pooled across the WHOLE CITY within an hour -
"how does a typical wait, anywhere in the city, behave right now". A stop with 1 bus/hour and a
stop with 20 buses/hour get one hex each on the map, but the busy stop contributes 20x the raw
observations to the chart - correctly, since that is how often the event actually happens.
Confirmed against real Lodz data 2026-08-17: whole-day per-stop median-of-medians (map) 17.6
min vs whole-day pooled-crossing median (chart, unbucketed) 7.0 min - both correct, for what
each is defined to measure.
"""
from __future__ import annotations

import pandas as pd

from transit_charts import quality, tidy


def _pooled_crossings(
    frame: pd.DataFrame, outages: list[tuple[pd.Timestamp, pd.Timestamp, float]]
) -> pd.DataFrame:
    """One row per observed crossing (after the first at its stop), with `headway_s` to the
    PREVIOUS vehicle of ANY line at the same `stop_id`.

    Mirrors `tidy._attach_headway`'s conventions - first vehicle at a stop gets no row (never a
    0-minute headway), and a gap spanning a feed outage is dropped rather than reported as a
    43-minute service gap nobody experienced.
    """
    subset = frame[frame.obs_time.notna()].sort_values("obs_time", kind="stable")
    grouped = subset.groupby("stop_id", dropna=False, sort=False)["obs_time"]
    previous = grouped.shift()
    out = subset.copy()
    out["headway_s"] = (out.obs_time - previous).dt.total_seconds()
    out = out[out.headway_s.notna()]

    if outages:
        has_previous = previous.loc[out.index]
        spans = quality.spans_outage(has_previous, out.obs_time, outages)
        out = out[~spans]
    return out


def per_stop_summary(
    frame: pd.DataFrame,
    outages: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    stop_locations: dict[str, tuple[float, float]],
    min_n: int = 3,
) -> pd.DataFrame:
    """One row per stop_id: cumulative median headway over the whole recording window.

    The map's input. `min_n=3` matches the convention already used for B5/B7/B8's grid cells - a
    median from two headways is not a measurement.
    """
    crossings = _pooled_crossings(frame, outages)
    grouped = crossings.groupby("stop_id", dropna=False)["headway_s"]
    stats = grouped.agg(n="count", median_headway_s="median").reset_index()
    stats["median_headway_min"] = stats.median_headway_s / 60.0
    stats["below_min_n"] = stats.n < min_n
    stats.loc[stats.below_min_n, "median_headway_min"] = float("nan")

    names = frame.drop_duplicates("stop_id").set_index("stop_id")["stop_name"]
    stats["stop_name"] = stats.stop_id.map(names)
    coords = stats.stop_id.map(stop_locations)
    stats["lat"] = coords.map(lambda c: c[0] if isinstance(c, tuple) else None)
    stats["lon"] = coords.map(lambda c: c[1] if isinstance(c, tuple) else None)
    missing_coords = int(stats.lat.isna().sum())
    return stats, missing_coords


def citywide_hourly(
    frame: pd.DataFrame,
    outages: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    bucket_minutes: int = 60,
    min_n: int = 20,
) -> pd.DataFrame:
    """One row per time-of-day bucket: the median headway across every crossing observed in
    that bucket, pooled over every stop and every line city-wide.

    Picture standing at a stop, timing the gap to the next arrival (any line), then repeating
    that at every stop in the city - this pools every one of those gaps that lands in the given
    hour and takes ONE median over the pool. Deliberately NOT a per-stop median computed first
    and then averaged across stops: that would give a stop with 1 crossing an hour the same vote
    as one with 100, which is not "the wait a passenger experiences", it's "the wait a stop
    has" - a different, spatial question that the cumulative, whole-window I37 map answers
    instead (Michal's call 2026-08-17, confirmed by hand-worked example against real Lodz data:
    pooled median ~7 min vs per-stop-then-averaged ~16.5 min in the same hour). The two numbers
    are NOT expected to match and neither is wrong - see the module docstring.
    """
    crossings = _pooled_crossings(frame, outages)
    crossings = crossings.copy()
    crossings["bucket"] = tidy.local_time_bucket(crossings.obs_local, bucket_minutes)
    return tidy.summarise(crossings, ["bucket"], "headway_s", min_n=min_n)
