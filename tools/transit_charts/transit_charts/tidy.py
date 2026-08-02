"""The one table every chart reads.

Design rule: **build this once, carefully; every chart is then a `groupby` over it.** The
alternative - each chart deriving delay or headway from raw crossings in its own way - is how
a set of figures ends up quietly disagreeing with each other.

Two consequences worth stating, because they are easy to undo by accident:

- Rejection reasons are carried, not applied. `seg_status` comes straight from
  `family_a.collect_stop_crossings` and each chart decides what it can tolerate. Speed and
  running-time charts must use `SEG_OK` only; headway charts do not care at all.
- Aggregation always reports `n`. `summarise()` has no mode that returns a statistic without
  the count behind it, so a chart cannot accidentally draw a median of three observations the
  same width as a median of three hundred.
"""
from __future__ import annotations

import zoneinfo
from datetime import timedelta

import pandas as pd

import sys
from pathlib import Path

_FAMILY_A = Path(__file__).resolve().parents[2] / "family_a_reconstruction"
if str(_FAMILY_A) not in sys.path:
    sys.path.insert(0, str(_FAMILY_A))

from family_a.calendar_scope import day_type_for_date  # noqa: E402

from transit_charts import quality, servicedate  # noqa: E402

# Columns downstream code may rely on. Kept as a constant so a chart can assert the schema it
# was written against rather than failing three transformations later on a missing column.
TIDY_COLUMNS = [
    "city", "service_date", "day_type", "recording_date",
    "trip_id", "route_id", "route_short_name", "route_group", "direction_id", "trip_headsign",
    "stop_sequence", "stop_id", "stop_name", "shape_dist_m",
    "sched_arr", "sched_dep", "obs_time", "obs_local", "delay_s",
    "seg_time_s", "sched_seg_time_s", "seg_dist_m", "seg_speed_kmh", "seg_status",
    "is_first_stop", "from_stop_id", "from_stop_name",
    "headway_s", "sched_headway_s", "headway_spans_outage", "headway_skips_vehicles",
    "trip_coverage", "service_date_offset_days", "service_date_plausible",
]


def build(
    crossings: pd.DataFrame,
    *,
    city: str,
    short_name_by_route: dict[str, str],
    group_by_route: dict[str, str],
    stop_names: dict[str, str],
    trip_headsigns: dict[str, str] | None = None,
    agency_tz: str,
    outages: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    report: quality.QualityReport,
) -> pd.DataFrame:
    """Assemble the tidy table from a `collect_stop_crossings` frame plus static context."""
    tz = zoneinfo.ZoneInfo(agency_tz)
    if crossings.empty:
        return pd.DataFrame(columns=TIDY_COLUMNS)

    frame = crossings.copy()
    frame["city"] = city
    frame["route_short_name"] = frame.route_id.map(short_name_by_route).fillna(frame.route_id)
    frame["route_group"] = frame.route_id.map(group_by_route).fillna(frame.route_short_name)
    frame["stop_name"] = frame.stop_id.map(stop_names).fillna(frame.stop_id)
    frame["trip_headsign"] = (
        frame.trip_id.map(trip_headsigns).fillna("") if trip_headsigns else ""
    )

    report.stops_total = len(frame)
    report.stops_crossed = int(frame.obs_time.notna().sum())

    frame = _attach_service_dates(frame, tz, report)
    # day_type comes from family_a's own rule, so a chart pooling several days can tell a
    # Saturday from a Tuesday. Without it, pooling a weekend into a weekday CV inflates the
    # result from a TIMETABLE difference and reads as unreliable service.
    frame["day_type"] = [
        day_type_for_date(d) if d is not None else None for d in frame.service_date
    ]
    frame = _attach_schedule_and_delay(frame, tz)
    frame = _attach_trip_coverage(frame)
    frame["seg_speed_kmh"] = _speed_kmh(frame.seg_dist_m, frame.seg_time_s)
    frame = _attach_segment_identity(frame, stop_names)
    frame = _attach_headway(frame, outages)

    frame["obs_local"] = frame.obs_time.dt.tz_convert(tz)
    # Reported here rather than in quality.py because the loop detection needs the per-trip
    # stop pattern, which only exists once the crossings have been assembled.
    repeated = frame.groupby(["trip_id", "recording_date"], dropna=False)["stop_id"].apply(
        lambda s: s.duplicated().any()
    )
    report.repeated_stop_trips = int(repeated.sum())

    return frame.reindex(columns=TIDY_COLUMNS)


def _attach_service_dates(frame: pd.DataFrame, tz, report: quality.QualityReport) -> pd.DataFrame:
    """Resolve each trip run's GTFS service date from its own observations (see servicedate.py)."""
    resolved: dict[tuple, tuple] = {}
    observed = frame[frame.obs_time.notna()]
    for key, group in observed.groupby(["trip_id", "recording_date"], dropna=False, sort=False):
        service_date, residual, plausible = servicedate.infer_service_date(
            group.sched_arr_s.tolist(), group.obs_time.tolist(), tz
        )
        observed_date = group.obs_time.iloc[0].tz_convert(tz).date()
        resolved[key] = (
            service_date,
            (service_date - observed_date).days,
            residual,
            plausible,
        )
    report.trips_implausible_service_date = sum(
        1 for value in resolved.values() if not value[3]
    )

    # A trip with no crossings at all cannot infer its own service date - and without one it
    # gets no absolute scheduled time either, so it disappears from anything schedule-based.
    # That matters beyond tidiness: an unobserved vehicle is exactly the one a headway needs to
    # know about (see _flag_skipped_vehicles), and it would be invisible. Fall back to the
    # modal service date of the same recording session, which is right for every trip that is
    # not an overnight tail, and leave None when nothing at all resolved.
    fallback: dict[object, object] = {}
    for (_trip, recording_date), value in resolved.items():
        fallback.setdefault(recording_date, []).append(value[0])
    modal = {
        recording_date: max(set(dates), key=dates.count)
        for recording_date, dates in fallback.items()
    }

    keys = list(zip(frame.trip_id, frame.recording_date))
    frame["service_date"] = [
        resolved[k][0] if k in resolved else modal.get(k[1]) for k in keys
    ]
    frame["service_date_offset_days"] = [
        resolved[k][1] if k in resolved else None for k in keys
    ]
    frame["service_date_plausible"] = [
        resolved[k][3] if k in resolved else True for k in keys
    ]
    return frame


def _attach_schedule_and_delay(frame: pd.DataFrame, tz) -> pd.DataFrame:
    """Absolute scheduled instants and the delay against them.

    Uses servicedate.gtfs_seconds_to_datetime per row rather than vectorised arithmetic on a
    midnight offset - the noon-minus-12h rule is the whole point and a fast wrong version is
    not an improvement. A trip whose service date could not be resolved (no crossings at all)
    gets NaT, not a guess.
    """

    def to_instant(service_date, seconds):
        if service_date is None or pd.isna(seconds):
            return pd.NaT
        return servicedate.gtfs_seconds_to_datetime(service_date, seconds, tz)

    frame["sched_arr"] = [
        to_instant(d, s) for d, s in zip(frame.service_date, frame.sched_arr_s)
    ]
    frame["sched_dep"] = [
        to_instant(d, s) for d, s in zip(frame.service_date, frame.sched_dep_s)
    ]
    frame["sched_arr"] = pd.to_datetime(frame["sched_arr"], utc=True)
    frame["sched_dep"] = pd.to_datetime(frame["sched_dep"], utc=True)
    frame["delay_s"] = (frame.obs_time - frame.sched_arr).dt.total_seconds()
    return frame


def _attach_trip_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """Fraction of each trip run's scheduled stops that produced a crossing.

    The guard against recording-window edges: a trip whose first half fell outside the window
    contributes only its later stops, which silently biases any aggregate keyed on
    stop_sequence. Charts that aggregate along the route filter on this; charts that do not
    (headway, time-of-day profiles) legitimately keep everything.
    """
    coverage = frame.groupby(["trip_id", "recording_date"], dropna=False)["obs_time"].transform(
        lambda s: s.notna().mean()
    )
    frame["trip_coverage"] = coverage
    return frame


def _attach_segment_identity(frame: pd.DataFrame, stop_names: dict[str, str]) -> pd.DataFrame:
    """Name the segment ending at each row, and give it its SCHEDULED duration.

    Scheduled travel is arrival here minus *departure* from the previous stop, so the previous
    stop's dwell is not counted twice - the same convention rebuild_stop_times uses when it
    accumulates a trip, which is what makes the two comparable in D17.
    """
    frame = frame.sort_values(["trip_id", "recording_date", "stop_sequence"], kind="stable")
    grouped = frame.groupby(["trip_id", "recording_date"], dropna=False, sort=False)
    frame["from_stop_id"] = grouped["stop_id"].shift()
    frame["from_stop_name"] = frame.from_stop_id.map(stop_names).fillna(frame.from_stop_id)
    previous_departure = grouped["sched_dep"].shift()
    frame["sched_seg_time_s"] = (frame.sched_arr - previous_departure).dt.total_seconds()
    # A negative scheduled duration means the static feed is inconsistent for that pair; it is
    # not a fast bus. Dropped to NaN so it cannot pull a padding median negative.
    frame.loc[frame.sched_seg_time_s < 0, "sched_seg_time_s"] = float("nan")
    return frame


def _speed_kmh(distance_m: pd.Series, seconds: pd.Series) -> pd.Series:
    """Implied average speed, NaN where the segment has no usable time (never inf)."""
    safe = seconds.where(seconds > 0)
    return distance_m / safe * 3.6


def _attach_headway(
    frame: pd.DataFrame, outages: list[tuple[pd.Timestamp, pd.Timestamp, float]]
) -> pd.DataFrame:
    """Seconds since the previous vehicle of the same route/direction crossed this stop.

    Keyed on (route_id, direction_id, stop_id, stop_sequence) - including the sequence, so a
    loop route that serves one stop twice per trip is treated as two distinct service points
    rather than having its two passes interleaved into a bogus 3-minute headway.

    The first vehicle in the recording window gets NaN, never 0: it has no predecessor, and a
    zero there would read as perfect bunching and poison every mean.
    """
    frame = frame.sort_values("obs_time", kind="stable")
    key = ["route_id", "direction_id", "stop_id", "stop_sequence"]
    observed = frame.obs_time.notna()

    previous = frame.loc[observed].groupby(key, dropna=False, sort=False)["obs_time"].shift()
    frame["headway_s"] = float("nan")
    frame.loc[observed, "headway_s"] = (
        frame.loc[observed, "obs_time"] - previous
    ).dt.total_seconds()

    # The scheduled gap for THE SAME PAIR OF VEHICLES, which means it has to be computed on the
    # observed rows only - not on every row that has a schedule.
    #
    # Getting this wrong is subtle and expensive. About 13% of scheduled stops produce no
    # crossing, so when a vehicle is missed the observed side silently merges two intervals
    # into one while a schedule-side computed over all rows does not. Measured on Łódź route
    # 11: observed median headway 16.16 min against a scheduled 15.00, a gap that is pure
    # measurement artifact. AWT is quadratic in headway, so that fed straight into B6 as
    # roughly a minute of excess wait that nobody experienced - the same order as the effect
    # the chart exists to show.
    #
    # Pairing on the observed rows makes both sides describe the same two vehicles. What
    # neither side can see is a trip that was never observed at all: `headway_skips_vehicles`
    # marks the intervals where the schedule says another vehicle should have been in between.
    observed_rows = frame.loc[observed].sort_values("obs_time", kind="stable")
    sched_previous = observed_rows.groupby(key, dropna=False, sort=False)["sched_arr"].shift()
    frame["sched_headway_s"] = float("nan")
    frame.loc[observed_rows.index, "sched_headway_s"] = (
        observed_rows.sched_arr - sched_previous
    ).dt.total_seconds()

    frame = _flag_skipped_vehicles(frame, key, observed)

    frame["headway_spans_outage"] = False
    if outages:  # noqa: SIM102 - kept flat; the block below is long enough already
        has_previous = previous.notna()
        if has_previous.any():
            spans = quality.spans_outage(
                previous[has_previous],
                frame.loc[previous[has_previous].index, "obs_time"],
                outages,
            )
            frame.loc[spans[spans].index, "headway_spans_outage"] = True
    return frame


def _flag_skipped_vehicles(frame: pd.DataFrame, key: list[str], observed: pd.Series):
    """Mark headways that span a scheduled vehicle nobody observed.

    Pairing the scheduled gap on the observed rows (above) makes both sides describe the same
    two vehicles, which is what a wait-time comparison needs. It also means a trip that ran but
    was never crossed is invisible to both - so this counts, per interval, how many scheduled
    arrivals of the same route/direction/stop fall strictly between the two observations. Any
    value above zero says "the timetable expected another vehicle here"; a chart can then
    exclude those intervals or report how many there were, rather than quietly averaging them.
    """
    frame["headway_skips_vehicles"] = 0
    scheduled_only = frame[frame.sched_arr.notna()]
    if scheduled_only.empty:
        return frame

    lookup = {
        group_key: group.sched_arr.sort_values().to_numpy()
        for group_key, group in scheduled_only.groupby(key, dropna=False, sort=False)
    }
    rows = frame.loc[observed & frame.headway_s.notna()]
    if rows.empty:
        return frame

    counts = []
    for record in rows.itertuples():
        arrivals = lookup.get(
            (record.route_id, record.direction_id, record.stop_id, record.stop_sequence)
        )
        if arrivals is None or pd.isna(record.sched_arr) or pd.isna(record.sched_headway_s):
            counts.append(0)
            continue
        # The window is bounded by the two vehicles' SCHEDULED arrivals, not their observed
        # ones. Mixing the two domains was the first version's mistake: a vehicle four minutes
        # late puts its own scheduled arrival well inside the observed interval, so on Łódź
        # route 11 it reported 1311 of 1464 intervals as skipping a vehicle - impossible at 87%
        # crossing coverage, and the giveaway that the comparison was not like for like.
        window_start = record.sched_arr - pd.Timedelta(seconds=float(record.sched_headway_s))
        between = ((arrivals > window_start) & (arrivals < record.sched_arr)).sum()
        counts.append(int(between))
    frame.loc[rows.index, "headway_skips_vehicles"] = counts
    return frame


def summarise(
    frame: pd.DataFrame,
    by: list[str],
    value: str,
    percentiles: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9),
    min_n: int = 1,
) -> pd.DataFrame:
    """Group and describe *value*, always returning `n` beside the statistics.

    There is deliberately no mean here. Every quantity in this tool is heavy-tailed - one
    vehicle stuck for twenty minutes moves a mean and barely moves a median - and a chart that
    silently used the mean would be describing the outlier, not the service.

    Rows below *min_n* are kept with their statistics set to NaN rather than dropped, so a
    renderer can draw "insufficient data" where the gap is instead of leaving a hole the reader
    will mistake for zero.
    """
    grouped = frame.dropna(subset=[value]).groupby(by, dropna=False, sort=True)[value]
    out = grouped.agg(n="count").reset_index()
    stats = grouped.quantile(list(percentiles)).unstack()
    stats.columns = [f"p{int(round(p * 100))}" for p in percentiles]
    out = out.merge(stats.reset_index(), on=by, how="left")
    thin = out.n < min_n
    out.loc[thin, [c for c in out.columns if c.startswith("p")]] = float("nan")
    out["below_min_n"] = thin
    return out


def observed(
    frame: pd.DataFrame,
    *,
    routes: list[str] | None = None,
    direction: str | None = None,
    include_first_stop: bool = False,
    min_trip_coverage: float | None = None,
    require_plausible_service_date: bool = True,
) -> pd.DataFrame:
    """The filtering every punctuality/trajectory chart starts from, in one place.

    *include_first_stop* defaults to False. A trip's first stop is where the terminus layover
    lands (FA-20), and while the geometry usually prevents it being interpolated at all, "usually"
    is not a filter. Charts that study the artifact turn it back on deliberately.

    *min_trip_coverage* is the recording-window guard. Only charts that aggregate ALONG the
    route need it - a trip clipped by the window contributes only its later stops and would
    tilt any per-stop_sequence statistic. Time-of-day charts legitimately keep everything.
    """
    out = frame[frame.obs_time.notna() & frame.delay_s.notna()]
    if routes:
        wanted = set(routes)
        out = out[out.route_short_name.isin(wanted) | out.route_group.isin(wanted)]
    if direction is not None:
        out = out[out.direction_id.astype(str) == str(direction)]
    if not include_first_stop:
        out = out[~out.is_first_stop.astype(bool)]
    if min_trip_coverage is not None:
        out = out[out.trip_coverage >= min_trip_coverage]
    if require_plausible_service_date and "service_date_plausible" in out.columns:
        out = out[out.service_date_plausible.fillna(True).astype(bool)]
    return out.copy()


def usable_headways(
    frame: pd.DataFrame,
    *,
    routes: list[str] | None = None,
    direction: str | None = None,
    include_outage_spanning: bool = False,
) -> pd.DataFrame:
    """Rows carrying a headway that describes the service rather than the recording.

    Two exclusions, both deliberate:

    - the first vehicle of each key has no predecessor and is already NaN, not 0;
    - a headway measured across a feed outage is an artefact of the recording. Reporting a
      40-minute "service gap" that was really the feed going quiet would misrepresent the
      operator, so those are dropped by default and counted in the quality report.

    Note what is NOT excluded: the first stop. Regularity does not use travel time at all, so
    the FA-20 layover artifact cannot reach it - this family is the one that stays clean.
    """
    out = frame[frame.headway_s.notna()]
    if routes:
        wanted = set(routes)
        out = out[out.route_short_name.isin(wanted) | out.route_group.isin(wanted)]
    if direction is not None:
        out = out[out.direction_id.astype(str) == str(direction)]
    if not include_outage_spanning:
        out = out[~out.headway_spans_outage.astype(bool)]
    return out.copy()


def wait_times(
    frame: pd.DataFrame,
    by: list[str],
    *,
    winsorise_quantile: float | None = 0.99,
    min_n: int = 5,
) -> pd.DataFrame:
    """Actual, scheduled and excess wait time per group - the B6 numbers.

    For passengers who turn up without consulting a timetable, the mean wait is not half the
    mean headway but ``E[H^2] / (2 E[H])`` - irregular service makes more passengers arrive
    into the long gaps. The excess over an evenly spaced service is what irregularity costs:

        AWT = E[H^2] / (2 E[H])      actual wait, from observed headways
        SWT = E[Hs^2] / (2 E[Hs])    the same formula on the SCHEDULED headways
        EWT = AWT - SWT              the part the operator did not plan

    Two things about this that have to stay visible rather than being buried:

    **It is quadratic in H, so one enormous gap dominates it.** A single 60-minute hole can
    swamp an hour of otherwise tidy 8-minute service. Hence *winsorise_quantile* - and hence
    the untrimmed value is returned too, always, so trimming can never hide a genuine collapse
    of service. If the two differ a lot, that difference is the finding.

    **Neither side sees a cancelled trip.** Both are computed from trips that were observed,
    so a route running half its buses perfectly evenly scores EWT near zero while its
    passengers wait twice as long. This measures irregularity, not lost capacity.
    """
    rows = []
    for key, group in frame.groupby(by, dropna=False, sort=True):
        observed = group.headway_s.dropna().astype(float)
        scheduled = group.sched_headway_s.dropna().astype(float)
        record_thin = len(observed) < min_n
        if record_thin:
            # Reported, not skipped: a group that vanishes leaves a hole in the chart, and a
            # hole reads as zero. Same rule as summarise().
            rows.append(
                dict(zip(by, key if isinstance(key, tuple) else (key,)))
                | {"n": len(observed), "below_min_n": True, "mean_headway_s": float("nan"),
                   "awt_s": float("nan"), "awt_untrimmed_s": float("nan"),
                   "swt_s": float("nan"), "ewt_s": float("nan"),
                   "ewt_untrimmed_s": float("nan")}
            )
            continue
        trimmed = observed
        if winsorise_quantile is not None and len(observed) > 2:
            cap = observed.quantile(winsorise_quantile)
            trimmed = observed.clip(upper=cap)
        record = dict(zip(by, key if isinstance(key, tuple) else (key,)))
        record.update(
            n=len(observed),
            below_min_n=False,
            mean_headway_s=float(observed.mean()),
            awt_s=_awt(trimmed),
            awt_untrimmed_s=_awt(observed),
            swt_s=_awt(scheduled) if len(scheduled) >= min_n else float("nan"),
        )
        record["ewt_s"] = record["awt_s"] - record["swt_s"]
        record["ewt_untrimmed_s"] = record["awt_untrimmed_s"] - record["swt_s"]
        rows.append(record)
    return pd.DataFrame(rows)


def _awt(headways: pd.Series) -> float:
    """E[H^2] / (2 E[H]) - the wait a randomly arriving passenger actually experiences."""
    if headways.empty or headways.mean() <= 0:
        return float("nan")
    return float((headways**2).mean() / (2 * headways.mean()))


def headway_cv(frame: pd.DataFrame, by: list[str], min_n: int = 3) -> pd.DataFrame:
    """Coefficient of variation of headway per group - the B5 numbers.

    Benchmark for reading it: below 0.25 is regarded as excellent regularity, and the US bus
    average sits near 0.42. Undefined below three observations (a standard deviation from two
    numbers is not a measurement), and those groups come back flagged rather than absent.
    """
    grouped = frame.dropna(subset=["headway_s"]).groupby(by, dropna=False, sort=True)["headway_s"]
    out = grouped.agg(n="count", mean="mean", std="std").reset_index()
    out["cv"] = out["std"] / out["mean"]
    out.loc[out.n < min_n, "cv"] = float("nan")
    out["below_min_n"] = out.n < min_n
    return out


def bunching_rate(
    frame: pd.DataFrame, by: list[str], threshold: float = 0.25, min_n: int = 3,
) -> pd.DataFrame:
    """Share of headways closed to under `threshold` of their OWN scheduled interval - the
    B8/H30 bunching-frequency statistic.

    Ratio to `sched_headway_s`, deliberately not a fixed number of minutes: a 5-minute service
    and a 20-minute service must land on the same axis, and they only do that when "bunched"
    means "closed to under a fraction of THIS pair's own interval" rather than "under N minutes"
    - a fixed-minute threshold would make the 20-minute service nearly incapable of "bunching"
    and the 5-minute one bunch under routine noise, two incomparable scales pretending to be one.

    Rows with no usable scheduled headway (`sched_headway_s` NaN, or <= 0) are dropped before
    grouping - same precedent as `wait_times`' SWT side, which has the identical gap. Undefined
    below `min_n` ratios, same convention as `headway_cv`: reported with `n`, not silently
    dropped, so a below-threshold group leaves a flagged row rather than a hole.
    """
    usable = frame.dropna(subset=["headway_s", "sched_headway_s"]).copy()
    usable = usable[usable.sched_headway_s > 0]
    usable["_ratio"] = usable.headway_s.astype(float) / usable.sched_headway_s.astype(float)
    usable["_bunched"] = usable["_ratio"] < threshold
    grouped = usable.groupby(by, dropna=False, sort=True)
    out = grouped.agg(n=("_ratio", "count"), bunched_share=("_bunched", "mean")).reset_index()
    out.loc[out.n < min_n, "bunched_share"] = float("nan")
    out["below_min_n"] = out.n < min_n
    return out


def usable_segments(
    frame: pd.DataFrame,
    *,
    routes: list[str] | None = None,
    direction: str | None = None,
) -> pd.DataFrame:
    """Rows whose measured segment survived every family_a filter - the D-family input.

    `seg_status == SEG_OK` is the whole guard, and it is not optional here. Speed and running
    time are exactly the quantities the FA-13/FA-18/FA-20 filters exist to protect: without
    this line a terminus layover is rendered as a 1.5 km/h "traffic jam", which is how these
    charts would lie most convincingly.
    """
    out = frame[(frame.seg_status == "ok") & frame.seg_time_s.notna()]
    if routes:
        wanted = set(routes)
        out = out[out.route_short_name.isin(wanted) | out.route_group.isin(wanted)]
    if direction is not None:
        out = out[out.direction_id.astype(str) == str(direction)]
    return out.copy()


def describe_days(frame: pd.DataFrame) -> tuple[int, list[str], str | None]:
    """(service days present, day types present, warning if pooling them is questionable).

    Every chart accepts repeated --table and concatenates, which is genuinely useful for the
    grid charts whose per-cell n is bounded by vehicles per hour. It is also silent, and that
    is the danger: a Saturday pooled into a weekday statistic differs because the TIMETABLE
    differs, not because the service was unreliable, and the result looks like a finding.
    """
    dates = sorted({str(d) for d in frame.service_date.dropna().unique()})
    types = sorted({str(t) for t in frame.get("day_type", pd.Series(dtype=object)).dropna().unique()})
    if len(dates) <= 1:
        return len(dates), types, None
    if len(types) > 1:
        return len(dates), types, (
            f"pooling {len(dates)} service days spanning {', '.join(types)} - these run "
            "DIFFERENT timetables, so any spread you see mixes unreliability with a planned "
            "difference. Filter to one day type, or read the result as a mixture."
        )
    return len(dates), types, (
        f"pooling {len(dates)} service days ({types[0] if types else 'unknown'}) into one "
        "series - n rises, but a single bad day is no longer visible as one"
    )


def busiest_direction(frame: pd.DataFrame) -> str:
    """The direction_id with the most observations - a sane default, reported not assumed.

    stop_sequence 5 is a different physical place in each direction, so any chart keyed on it
    has to pick one. Silently mixing them would produce a plausible-looking average of two
    unrelated places.
    """
    if frame.empty:
        return "0"
    return str(frame.direction_id.astype(str).value_counts().idxmax())


def direction_label(frame: pd.DataFrame) -> str:
    """What the vehicles of an already direction-filtered frame say on the front.

    `direction 1` is a feed-internal key and tells a reader nothing; "Chocianowice IKEA" is the
    same fact in the form they meet it at the stop. The key still belongs in the title beside
    it, because it is what `--direction` takes.

    Two fallbacks, in order, because `trip_headsign` is optional in GTFS and blank in several of
    the feeds here: the modal non-empty headsign, then the name of the last stop of the longest
    trip pattern in the frame. Returns "" when neither exists, and the caller then titles the
    chart with the bare direction rather than inventing a terminus.
    """
    if frame.empty:
        return ""
    if "trip_headsign" in frame.columns:
        headsigns = frame.trip_headsign.dropna().astype(str)
        headsigns = headsigns[headsigns.str.strip() != ""]
        if not headsigns.empty:
            return str(headsigns.value_counts().idxmax())
    if "stop_name" not in frame.columns:
        return ""
    # The longest pattern, not simply the highest stop_sequence: a short-turn variant ends
    # somewhere in the middle of the route and naming the chart after it would be wrong.
    last = frame.loc[frame.groupby(["trip_id", "recording_date"], dropna=False).stop_sequence
                     .idxmax()]
    if last.empty:
        return ""
    return str(last.stop_name.value_counts().idxmax())


def route_direction_title(route: str, direction: str, label: str) -> str:
    """`route 11 -> Chocianowice IKEA (direction 1)`, or without the arrow when unlabelled."""
    scope = f"route {route}"
    if label:
        scope += f" -> {label}"
    return f"{scope} (direction {direction})"


def local_time_bucket(series: pd.Series, minutes: int) -> pd.Series:
    """Floor a tz-aware local timestamp series to a bucket of *minutes*, as a time-of-day.

    Returns minutes-since-local-midnight so charts can share one numeric x axis across days.
    """
    seconds = series.dt.hour * 3600 + series.dt.minute * 60 + series.dt.second
    return (seconds // (minutes * 60)) * minutes


def elapsed_since_trip_start(frame: pd.DataFrame) -> pd.Series:
    """Minutes since each trip run's first observed crossing - the y axis of A2."""
    first = frame.groupby(["trip_id", "recording_date"], dropna=False)["obs_time"].transform("min")
    return (frame.obs_time - first).dt.total_seconds() / 60.0
