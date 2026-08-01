"""Data-quality guards, each one written because a real archive tripped over it.

Nothing here silently discards data. Every function either **labels** rows or **reports**
counts, and the extraction step prints the report. The reason is a specific incident: Lisbon's
matched table for 2026-07-28 carries 944 observations on each of three foreign dates, three
weeks stale, and that was only discovered by auditing recording windows by hand. A pipeline
that had quietly dropped them would have been just as wrong and much harder to question.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# A gap longer than this in the WHOLE city's observation stream is a feed outage, not thin
# service - during it no vehicle anywhere reports, which no real timetable produces. Chosen
# well above the 30-60 s polling interval these archives were recorded at.
DEFAULT_OUTAGE_GAP_S = 300.0

# Observations further than this from the table's dominant recording date are a different day's
# data leaking in. One day of slack absorbs a legitimate overnight tail.
DEFAULT_MAX_DATE_DRIFT_DAYS = 1


@dataclass
class QualityReport:
    """Everything the extraction noticed but did not act on unilaterally."""

    observations_in: int = 0
    observations_kept: int = 0
    stale_observations: int = 0
    stale_dates: dict[str, int] = field(default_factory=dict)
    dominant_recording_date: str | None = None
    outages: list[tuple[pd.Timestamp, pd.Timestamp, float]] = field(default_factory=list)
    multi_vehicle_suspect_trips: int = 0
    repeated_stop_trips: int = 0
    trips_implausible_service_date: int = 0
    stops_total: int = 0
    stops_crossed: int = 0

    @property
    def crossing_rate(self) -> float:
        return self.stops_crossed / self.stops_total if self.stops_total else 0.0

    def render(self) -> str:
        lines = [
            "Data quality",
            f"  observations              : {self.observations_in:,} in, "
            f"{self.observations_kept:,} kept",
        ]
        if self.stale_observations:
            detail = ", ".join(f"{d} x{n:,}" for d, n in sorted(self.stale_dates.items()))
            lines.append(
                f"  stale-date observations   : {self.stale_observations:,} dropped "
                f"(dominant date {self.dominant_recording_date}; found {detail})"
            )
        else:
            lines.append("  stale-date observations   : none")
        if self.outages:
            worst = max(self.outages, key=lambda o: o[2])
            lines.append(
                f"  feed outages (> gap limit): {len(self.outages)}, longest "
                f"{worst[2] / 60:.1f} min at {worst[0]:%H:%M}"
            )
        else:
            lines.append("  feed outages              : none")
        lines.append(
            f"  stop crossings            : {self.stops_crossed:,}/{self.stops_total:,} "
            f"({self.crossing_rate * 100:.1f}%)"
        )
        lines.append(
            "    NOTE: the misses are not random. A vehicle that drops out of the feed WHILE "
            "STUCK contributes nothing, so every delay curve here is biased optimistic."
        )
        if self.multi_vehicle_suspect_trips:
            lines.append(
                f"  trips with a non-monotonic distance series (two vehicles on one trip_id?): "
                f"{self.multi_vehicle_suspect_trips:,}"
            )
        if self.repeated_stop_trips:
            lines.append(
                f"  trips visiting a stop_id twice (loop routes): {self.repeated_stop_trips:,}"
            )
        if self.trips_implausible_service_date:
            lines.append(
                f"  trips whose best service date still misfits: "
                f"{self.trips_implausible_service_date:,} (flagged, not dropped)"
            )
        return "\n".join(lines)


def drop_stale_observations(
    matched: pd.DataFrame,
    report: QualityReport,
    max_drift_days: int = DEFAULT_MAX_DATE_DRIFT_DAYS,
) -> pd.DataFrame:
    """Remove observations whose own date is far from the table's dominant recording date.

    Real case this exists for - Lisbon 2026-07-28::

        2026-07-08:      944      2026-07-21:      944
        2026-07-22:      944      2026-07-28:  327,003

    Exactly 944 on each foreign date is not noise; it looks like vehicles reporting a frozen
    timestamp, or the feed replaying stale records. Harmless to FA-6, which groups by
    recording_date, but fatal to anything keyed on time of day: three-week-old observations
    would land in the "Tuesday 14:00" bucket.
    """
    report.observations_in = len(matched)
    if matched.empty:
        return matched

    observed_dates = matched["timestamp"].dt.date
    dominant = observed_dates.mode().iloc[0]
    report.dominant_recording_date = str(dominant)

    drift_days = (pd.to_datetime(observed_dates) - pd.Timestamp(dominant)).dt.days.abs()
    stale = drift_days > max_drift_days
    if stale.any():
        counts = observed_dates[stale].astype(str).value_counts()
        report.stale_dates = {str(k): int(v) for k, v in counts.items()}
        report.stale_observations = int(stale.sum())

    kept = matched[~stale].copy()
    report.observations_kept = len(kept)
    return kept


def find_outages(
    matched: pd.DataFrame, gap_s: float = DEFAULT_OUTAGE_GAP_S
) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
    """Intervals during which the whole feed went silent, as (start, end, seconds).

    Computed across all vehicles, not per route: a single route having no bus for 40 minutes at
    23:00 is a timetable, whereas *nothing anywhere* reporting for 40 minutes is an outage. The
    distinction matters because a headway measured across an outage is an artefact of the
    recording, and reporting it as a service gap would be a lie about the operator.
    """
    if matched.empty:
        return []
    stamps = matched["timestamp"].sort_values().to_numpy()
    if len(stamps) < 2:
        return []
    deltas = (stamps[1:] - stamps[:-1]) / pd.Timedelta(seconds=1)
    return [
        (pd.Timestamp(stamps[i]), pd.Timestamp(stamps[i + 1]), float(deltas[i]))
        for i in range(len(deltas))
        if deltas[i] > gap_s
    ]


def spans_outage(
    start: pd.Series, end: pd.Series, outages: list[tuple[pd.Timestamp, pd.Timestamp, float]]
) -> pd.Series:
    """Elementwise: does the interval [start, end] overlap any outage?"""
    flag = pd.Series(False, index=start.index)
    for out_start, out_end, _seconds in outages:
        flag |= (start < out_end) & (end > out_start)
    return flag


def flag_non_monotonic_trips(matched: pd.DataFrame, tolerance_m: float = 100.0) -> set[str]:
    """trip_ids whose distance series moves backwards by more than GPS noise explains.

    FA-2 documents small backward jumps on ~9% of real trips and `interpolate_stop_time` is
    built to tolerate them. A *large* reversal is a different animal: most likely two vehicles
    sharing one trip_id, whose interleaved positions would produce nonsense crossings. Flagged
    for the report rather than dropped, because the honest count is more useful than a guess.
    """
    if matched.empty:
        return set()
    # Grouped by (trip_id, recording_date) like everything else since FA-6: GTFS-RT trip_ids
    # are not date-qualified, so a two-day table grouped on trip_id alone would see yesterday's
    # last position followed by today's first and report a false "two vehicles" for every trip.
    key = ["trip_id", "recording_date"] if "recording_date" in matched.columns else ["trip_id"]
    ordered = matched.sort_values([*key, "timestamp"])
    delta = ordered.groupby(key, sort=False, dropna=False)["distance_along_shape_m"].diff()
    return set(ordered.loc[delta < -tolerance_m, "trip_id"].unique())
