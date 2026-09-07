"""Pure, bounded-hold viewer metrics. Missing time is never zero-filled."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import fsum

from .persistence import PersistenceError


def timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise PersistenceError("invalid_timestamp", "analytics")
    return value


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise PersistenceError("invalid_identifier", "analytics")
    return value


@dataclass(frozen=True, slots=True)
class CollectionRun:
    id: str
    started_at: datetime
    stopped_at: datetime | None = None

    def __post_init__(self):
        identifier(self.id)
        timestamp(self.started_at)
        if self.stopped_at is not None and timestamp(self.stopped_at) < self.started_at:
            raise PersistenceError("invalid_range", "analytics")


@dataclass(frozen=True, slots=True)
class ViewerObservation:
    run_id: str
    observed_at: datetime
    viewer_count: int

    def __post_init__(self):
        identifier(self.run_id)
        timestamp(self.observed_at)
        if type(self.viewer_count) is not int or not 0 <= self.viewer_count <= 2**63 - 1:
            raise PersistenceError("invalid_viewer_count", "analytics")


@dataclass(frozen=True, slots=True)
class ObservationGap:
    id: str
    started_at: datetime
    ended_at: datetime | None
    reason: str

    def __post_init__(self):
        identifier(self.id)
        timestamp(self.started_at)
        if self.ended_at is not None and timestamp(self.ended_at) <= self.started_at:
            raise PersistenceError("invalid_range", "analytics")
        if self.reason not in ("request_failed", "stopped", "disconnected", "unknown"):
            raise PersistenceError("invalid_gap_reason", "analytics")


@dataclass(frozen=True, slots=True)
class ViewerSegment:
    start: datetime
    end: datetime
    viewer_count: int


@dataclass(frozen=True, slots=True)
class ViewerMetrics:
    range_start: datetime
    range_end: datetime
    weighted_viewer_seconds: float
    covered_seconds: float
    coverage_ratio: float | None
    average_viewers: float | None
    max_viewers: int | None
    peak_at: datetime | None
    segments: tuple[ViewerSegment, ...]
    method: str = "bounded_hold_v1"


def calculate_viewers(runs, observations, gaps, start, end, *, duration_known=True, include_endpoint=False):
    """Half-open analysis window; optionally include a live endpoint in peak only.

    Closed gaps invalidate prior values even after the gap ends: only a fresh
    observation can resume coverage. Adjacent runs never share held values.
    """
    timestamp(start)
    timestamp(end)
    if end < start:
        raise PersistenceError("invalid_range", "analytics")
    ordered_runs = sorted(runs, key=lambda r: r.started_at)
    by_id = {r.id: r for r in ordered_runs}
    if len(by_id) != len(ordered_runs):
        raise PersistenceError("duplicate_run", "analytics")
    for left, right in zip(ordered_runs, ordered_runs[1:]):
        if left.stopped_at is None or left.stopped_at > right.started_at:
            raise PersistenceError("overlapping_runs", "analytics")
    ordered = sorted(observations, key=lambda o: o.observed_at)
    if len({o.observed_at for o in ordered}) != len(ordered):
        raise PersistenceError("duplicate_observation", "analytics")
    gaps = sorted(gaps, key=lambda g: g.started_at)
    for left, right in zip(gaps, gaps[1:]):
        if left.ended_at is None or left.ended_at > right.started_at:
            raise PersistenceError("overlapping_gaps", "analytics")
    segments, peaks = [], []
    gap_index = 0
    for index, observation in enumerate(ordered):
        run = by_id.get(observation.run_id)
        at = observation.observed_at
        if run is None or at < run.started_at or (run.stopped_at is not None and at >= run.stopped_at):
            raise PersistenceError("observation_outside_run", "analytics")
        # Gaps are disjoint, so one forward scan suffices.
        while gap_index < len(gaps) and gaps[gap_index].ended_at is not None and gaps[gap_index].ended_at <= at:
            gap_index += 1
        gap = gaps[gap_index] if gap_index < len(gaps) else None
        if gap is not None and gap.started_at <= at:
            continue
        if start <= at < end or (include_endpoint and start <= at == end):
            peaks.append(observation)
        stop = min(at + timedelta(seconds=30), end)
        if run.stopped_at is not None:
            stop = min(stop, run.stopped_at)
        if index + 1 < len(ordered):
            stop = min(stop, ordered[index + 1].observed_at)
        if gap is not None:
            stop = min(stop, gap.started_at)
        begin = max(start, at)
        if begin < stop:
            segments.append(ViewerSegment(begin, stop, observation.viewer_count))
    seconds = [(s.end - s.start).total_seconds() for s in segments]
    covered = fsum(seconds)
    weighted = fsum(s.viewer_count * w for s, w in zip(segments, seconds))
    duration = (end - start).total_seconds()
    peak = max(peaks, key=lambda o: o.viewer_count) if peaks else None
    return ViewerMetrics(start, end, weighted, covered,
                         covered / duration if duration_known and duration > 0 else None,
                         weighted / covered if covered else None,
                         peak.viewer_count if peak else None, peak.observed_at if peak else None,
                         tuple(segments))


def graph_intervals(metrics, budget=1200):
    """Bounded graph envelopes, preserving every gap and each bucket's extrema.

    Too many discontinuities produce an explicit unavailable graph rather than
    joining gaps or silently truncating. Summary metrics always use raw data.
    """
    if type(budget) is not int or not 32 <= budget <= 5000:
        raise PersistenceError("invalid_point_budget", "analytics")
    segments = metrics.segments
    breaks = sum(left.end != right.start for left, right in zip(segments, segments[1:]))
    if breaks >= budget:
        return "range_required", ()
    groups = []
    bins = budget - breaks
    duration = (metrics.range_end - metrics.range_start).total_seconds()
    for s in segments:
        bucket = int((s.start - metrics.range_start).total_seconds() / duration * bins)
        if len(segments) <= budget or not groups or groups[-1][0] != bucket or groups[-1][-1] != s.start:
            groups.append([bucket, s.start, s.viewer_count, s.viewer_count, s.viewer_count, s.viewer_count, s.end])
        else:
            group = groups[-1]
            group[3] = min(group[3], s.viewer_count)
            group[4] = max(group[4], s.viewer_count)
            group[5] = s.viewer_count
            group[6] = s.end
    if len(groups) > budget:
        return "range_required", ()
    return ("raw" if len(segments) <= budget else "min_max_first_last"), tuple(
        {"start": g[1], "end": g[6], "first": g[2], "min": g[3], "max": g[4], "last": g[5]} for g in groups)
