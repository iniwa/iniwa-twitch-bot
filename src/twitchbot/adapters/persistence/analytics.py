"""Explicit analytics writes and transaction-consistent, read-only history queries."""

from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
import math
import sqlite3

from ...application.analytics import (CollectionRun, ObservationGap, ViewerObservation,
                                      calculate_viewers, graph_intervals, identifier)
from ...application.persistence import PersistenceError
from .sqlite import SQLiteDatabase, from_rfc3339, to_rfc3339, utc_now


class AnalyticsRepository:
    def __init__(self, database: SQLiteDatabase):
        self.database = database

    @contextmanager
    def _write(self, stream_id):
        identifier(stream_id)
        try:
            with self.database.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if connection.execute("SELECT 1 FROM streams WHERE id=?", (stream_id,)).fetchone() is None:
                        raise PersistenceError("stream_not_found", "analytics")
                    yield connection
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise PersistenceError("analytics_write_failed", "analytics") from error

    def start_run(self, stream_id, run: CollectionRun):
        with self._write(stream_id) as c:
            values = (stream_id, run.id, to_rfc3339(run.started_at),
                      to_rfc3339(run.stopped_at) if run.stopped_at else None)
            old = c.execute("SELECT * FROM collection_runs WHERE stream_id=? AND id=?", values[:2]).fetchone()
            if old is not None:
                if tuple(old) == values:
                    return False
                raise PersistenceError("record_conflict", "analytics")
            overlap = c.execute("SELECT 1 FROM collection_runs WHERE stream_id=? AND (stopped_at IS NULL OR stopped_at>?) AND (? IS NULL OR started_at<?)",
                                (stream_id, values[2], values[3], values[3])).fetchone()
            if overlap:
                raise PersistenceError("overlapping_runs", "analytics")
            c.execute("INSERT INTO collection_runs VALUES (?,?,?,?)", values)
            return True

    def stop_run(self, stream_id, run_id, stopped_at):
        identifier(run_id)
        at = to_rfc3339(stopped_at)
        with self._write(stream_id) as c:
            row = c.execute("SELECT * FROM collection_runs WHERE stream_id=? AND id=?", (stream_id, run_id)).fetchone()
            if row is None:
                raise PersistenceError("run_not_found", "analytics")
            if row["stopped_at"] == at:
                return False
            if row["stopped_at"] is not None or at < row["started_at"]:
                raise PersistenceError("record_conflict", "analytics")
            if c.execute("SELECT 1 FROM viewer_observations WHERE stream_id=? AND run_id=? AND observed_at>=?", (stream_id, run_id, at)).fetchone():
                raise PersistenceError("observation_outside_run", "analytics")
            c.execute("UPDATE collection_runs SET stopped_at=? WHERE stream_id=? AND id=?", (at, stream_id, run_id))
            return True

    def append(self, stream_id, observation: ViewerObservation):
        at = to_rfc3339(observation.observed_at)
        values = (stream_id, observation.run_id, at, observation.viewer_count)
        with self._write(stream_id) as c:
            old = c.execute("SELECT * FROM viewer_observations WHERE stream_id=? AND observed_at=?", (stream_id, at)).fetchone()
            if old is not None:
                if tuple(old) == values:
                    return False
                raise PersistenceError("record_conflict", "analytics")
            run = c.execute("SELECT * FROM collection_runs WHERE stream_id=? AND id=?", values[:2]).fetchone()
            if run is None or at < run["started_at"] or (run["stopped_at"] is not None and at >= run["stopped_at"]):
                raise PersistenceError("observation_outside_run", "analytics")
            if c.execute("SELECT 1 FROM observation_gaps WHERE stream_id=? AND started_at<=? AND (ended_at IS NULL OR ended_at>?)", (stream_id, at, at)).fetchone():
                raise PersistenceError("observation_inside_gap", "analytics")
            c.execute("INSERT INTO viewer_observations VALUES (?,?,?,?)", values)
            return True

    def save_gap(self, stream_id, gap: ObservationGap):
        """Insert a gap, or close an open gap without changing its identity."""
        values = (stream_id, gap.id, to_rfc3339(gap.started_at),
                  to_rfc3339(gap.ended_at) if gap.ended_at else None, gap.reason)
        with self._write(stream_id) as c:
            old = c.execute("SELECT * FROM observation_gaps WHERE stream_id=? AND id=?", values[:2]).fetchone()
            if old is not None:
                if tuple(old) == values:
                    return False
                if old["ended_at"] is not None or old["started_at"] != values[2] or old["reason"] != gap.reason or gap.ended_at is None:
                    raise PersistenceError("record_conflict", "analytics")
            if c.execute("SELECT 1 FROM observation_gaps WHERE stream_id=? AND id<>? AND (ended_at IS NULL OR ended_at>?) AND (? IS NULL OR started_at<?)",
                         (stream_id, gap.id, values[2], values[3], values[3])).fetchone():
                raise PersistenceError("overlapping_gaps", "analytics")
            if c.execute("SELECT 1 FROM viewer_observations WHERE stream_id=? AND observed_at>=? AND (? IS NULL OR observed_at<?)",
                         (stream_id, values[2], values[3], values[3])).fetchone():
                raise PersistenceError("observation_inside_gap", "analytics")
            if old is None:
                c.execute("INSERT INTO observation_gaps VALUES (?,?,?,?,?)", values)
            else:
                c.execute("UPDATE observation_gaps SET ended_at=? WHERE stream_id=? AND id=?", (values[3], stream_id, gap.id))
            return True

    def set_end_precision(self, stream_id, precision):
        if precision not in ("confirmed", "estimated", "unknown"):
            raise PersistenceError("invalid_end_precision", "analytics")
        with self._write(stream_id) as c:
            c.execute("INSERT INTO stream_metric_state(stream_id,revision,end_precision) VALUES (?,1,?) ON CONFLICT(stream_id) DO UPDATE SET end_precision=excluded.end_precision,revision=revision+1 WHERE end_precision<>excluded.end_precision", (stream_id, precision))


class HistoryReader:
    """Only an explicitly supplied candidate database can be queried.

    No migrations, default database discovery, adapter calls, or runtime startup.
    All streams in one comparison use one SQLite snapshot and one as-of time.
    """
    MAX_ROWS = 100_000

    def __init__(self, database: SQLiteDatabase, *, clock=utc_now):
        self.database = database
        self.clock = clock

    @contextmanager
    def _read(self):
        c = None
        try:
            c = sqlite3.connect(self.database.path.as_uri() + "?mode=ro", uri=True)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("BEGIN")
            yield c
        except sqlite3.Error as error:
            raise PersistenceError("history_unavailable", "history") from error
        finally:
            if c is not None:
                c.close()

    def _rows(self, c, table, stream_id, *, start=None, end=None):
        # Table identifiers are internal constants, never request input.
        clause = ""
        params = [stream_id]
        if table == "viewer_observations" and start is not None:
            clause = " AND observed_at>=? AND observed_at<?"
            params.extend((to_rfc3339(start - timedelta(seconds=30)), to_rfc3339(end)))
        rows = c.execute(f"SELECT * FROM {table} WHERE stream_id=?{clause} LIMIT ?", (*params, self.MAX_ROWS + 1)).fetchall()
        if len(rows) > self.MAX_ROWS:
            raise PersistenceError("history_limit_exceeded", "history")
        return rows

    def _stream(self, c, stream_id):
        identifier(stream_id)
        row = c.execute("SELECT * FROM streams WHERE id=?", (stream_id,)).fetchone()
        if row is None:
            raise PersistenceError("stream_not_found", "history")
        return row

    def _analytics(self, c, stream, as_of, *, end_override=None, start_override=None, point_budget=1200, include_graph=True):
        stream_id = stream["id"]
        start = from_rfc3339(stream["started_at"])
        ended = from_rfc3339(stream["ended_at"]) if stream["ended_at"] else None
        end = min(ended, as_of) if ended else as_of
        state = c.execute("SELECT * FROM stream_metric_state WHERE stream_id=?", (stream_id,)).fetchone()
        precision = state["end_precision"] if state else "unknown"
        if end_override is not None:
            end = min(end, end_override)
        if start_override is not None:
            start = max(start, start_override)
        runs = [CollectionRun(r["id"], from_rfc3339(r["started_at"]), from_rfc3339(r["stopped_at"]) if r["stopped_at"] else None) for r in self._rows(c, "collection_runs", stream_id)]
        observations = [ViewerObservation(r["run_id"], from_rfc3339(r["observed_at"]), r["viewer_count"]) for r in self._rows(c, "viewer_observations", stream_id, start=start, end=end)]
        gaps = [ObservationGap(r["id"], from_rfc3339(r["started_at"]), from_rfc3339(r["ended_at"]) if r["ended_at"] else None, r["reason"]) for r in self._rows(c, "observation_gaps", stream_id)]
        metrics = calculate_viewers(runs, observations, gaps, start, end,
                                    duration_known=ended is not None and precision != "unknown")
        result = {key: getattr(metrics, key) for key in ("method", "weighted_viewer_seconds", "covered_seconds", "coverage_ratio", "average_viewers", "max_viewers")}
        result.update(id=stream_id, title=stream["title"], game_name=stream["game_name"],
                      started_at=stream["started_at"], ended_at=stream["ended_at"],
                      range_start=to_rfc3339(start), range_end=to_rfc3339(end), as_of=to_rfc3339(as_of),
                      data_revision=f"{stream['revision']}:{state['revision'] if state else 0}",
                      peak_at=to_rfc3339(metrics.peak_at) if metrics.peak_at else None,
                      end_precision=precision,
                      quality="no_observations" if not metrics.segments else "partial" if metrics.covered_seconds < (end-start).total_seconds() else "covered",
                      metric_quality={"average": "observed_time_only" if metrics.average_viewers is not None else "unavailable",
                                      "maximum": "observed_points_only" if metrics.max_viewers is not None else "unavailable",
                                      "coverage": precision if metrics.coverage_ratio is not None else "unavailable"},
                       legacy_metrics={"method": "legacy", "average_viewers": stream["average_viewers"], "max_viewers": stream["max_viewers"]})
        if include_graph:
            graph_method, intervals = graph_intervals(metrics, point_budget)
            result.update(graph={"method": graph_method, "point_budget": point_budget, "range_start": to_rfc3339(start), "range_end": to_rfc3339(end)},
                          segments=[{**s, "start": to_rfc3339(s["start"]), "end": to_rfc3339(s["end"])} for s in intervals])
        return result

    @staticmethod
    def _list_revision(c, rows):
        states = {row['stream_id']: (row['revision'], row['end_precision']) for row in c.execute('SELECT stream_id,revision,end_precision FROM stream_metric_state')}
        payload = '\n'.join(f"{row['id']}:{row['revision']}:{states.get(row['id'], (0, 'unknown'))[0]}:{states.get(row['id'], (0, 'unknown'))[1]}" for row in sorted(rows, key=lambda item: item['id']))
        return sha256(payload.encode()).hexdigest()

    def list_streams(self, *, limit=50, before=None, sort='started_at', order='desc'):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PersistenceError("invalid_limit", "history")
        if sort not in ('started_at', 'average_viewers', 'max_viewers', 'coverage_ratio') or order not in ('asc', 'desc'):
            raise PersistenceError('invalid_sort', 'history')
        legacy = before is not None and isinstance(before, (list, tuple))
        if legacy and (sort, order) != ('started_at', 'desc'):
            raise PersistenceError('invalid_cursor', 'history')
        if legacy:
            if len(before) != 2:
                raise PersistenceError('invalid_cursor', 'history')
            to_rfc3339(from_rfc3339(before[0]))
            identifier(before[1])
        elif before is not None:
            expected = {'v', 'sort', 'order', 'as_of', 'revision', 'value', 'id'}
            if not isinstance(before, dict) or set(before) != expected or before.get('v') != 1 or before.get('sort') != sort or before.get('order') != order:
                raise PersistenceError('invalid_cursor', 'history')
            identifier(before.get('id'))
            if not isinstance(before.get('revision'), str) or len(before['revision']) != 64 or any(char not in '0123456789abcdef' for char in before['revision']):
                raise PersistenceError('invalid_cursor', 'history')
            if sort == 'started_at':
                to_rfc3339(from_rfc3339(before.get('value')))
            elif before.get('value') is not None and (type(before['value']) not in (int, float) or isinstance(before['value'], bool) or not math.isfinite(before['value'])):
                raise PersistenceError('invalid_cursor', 'history')
        as_of = from_rfc3339(before['as_of']) if isinstance(before, dict) else self.clock()
        with self._read() as c:
            rows = c.execute('SELECT * FROM streams').fetchall()
            revision = self._list_revision(c, rows)
            if isinstance(before, dict) and before['revision'] != revision:
                raise PersistenceError('list_changed', 'history')
            if sort == 'started_at':
                rows = sorted(rows, key=lambda row: (row['started_at'], row['id']), reverse=order == 'desc')
                summaries = [(row['started_at'], row['id'], row) for row in rows]
            else:
                summaries = []
                for row in rows:
                    item = self._analytics(c, row, as_of, include_graph=False)
                    summaries.append((item[sort], item['id'], item))
                known = sorted((item for item in summaries if item[0] is not None), key=lambda item: (item[0], item[1]), reverse=order == 'desc')
                missing = sorted((item for item in summaries if item[0] is None), key=lambda item: item[1], reverse=order == 'desc')
                summaries = known + missing
            if legacy:
                summaries = [item for item in summaries if (item[0], item[1]) < tuple(before)]
            elif isinstance(before, dict):
                index = next((n for n, item in enumerate(summaries) if item[1] == before['id'] and item[0] == before['value']), None)
                if index is None:
                    raise PersistenceError('list_changed', 'history')
                summaries = summaries[index + 1:]
            selected = summaries[:limit]
            items = []
            for _, _, value in selected:
                item = value if isinstance(value, dict) else self._analytics(c, value, as_of, include_graph=False)
                items.append(item)
            last = selected[-1] if len(summaries) > limit else None
            cursor = {'v': 1, 'sort': sort, 'order': order, 'as_of': to_rfc3339(as_of),
                      'revision': revision, 'value': last[0], 'id': last[1]} if last else None
            return {'items': items, 'next_cursor': cursor, 'as_of': to_rfc3339(as_of), 'sort': sort, 'order': order}

    def detail(self, stream_id, *, start=None, end=None, point_budget=1200):
        with self._read() as c:
            return self._analytics(c, self._stream(c, stream_id), self.clock(), start_override=start, end_override=end, point_budget=point_budget)

    def compare(self, stream_ids, *, scope="full"):
        if len(stream_ids) != 2 or stream_ids[0] == stream_ids[1] or scope not in ("full", "common"):
            raise PersistenceError("invalid_comparison", "history")
        as_of = self.clock()
        with self._read() as c:
            rows = [self._stream(c, sid) for sid in stream_ids]
            ends = [None, None]
            if scope == "common":
                states = [c.execute("SELECT end_precision FROM stream_metric_state WHERE stream_id=?", (r["id"],)).fetchone() for r in rows]
                if any(r["ended_at"] is None for r in rows) or any(s is None or s["end_precision"] == "unknown" for s in states):
                    raise PersistenceError("comparison_duration_unknown", "history")
                duration = min(min(from_rfc3339(r["ended_at"]), as_of) - from_rfc3339(r["started_at"]) for r in rows)
                ends = [from_rfc3339(r["started_at"]) + duration for r in rows]
            items = [self._analytics(c, r, as_of, end_override=end) for r, end in zip(rows, ends)]
            left, right = (r["average_viewers"] for r in items)
            delta = right - left if left is not None and right is not None else None
            return {"items": items, "scope": scope, "as_of": to_rfc3339(as_of), "average_difference": delta,
                    "average_change_ratio": delta / left if delta is not None and left else None}
