"""Atomic stream observations and channel cache publication for one recorder."""

from datetime import timedelta
import json
from uuid import uuid4

from ...application.live import StreamSnapshot
from ...application.persistence import PersistenceError
from .community import CommunityRepository
from .sqlite import from_rfc3339, to_rfc3339


class RecordingRepository:
    def __init__(self, database, channel_id, *, clock):
        self.records = CommunityRepository(database, channel_id, clock=clock)
        self.run_id = uuid4().hex

    def recover(self):
        # A crashed process cannot testify that it collected through the downtime.
        with self.records.transaction(write=True) as c:
            rows = c.execute('SELECT r.stream_id,r.id,r.started_at,MAX(v.observed_at) AS last FROM collection_runs r LEFT JOIN viewer_observations v ON v.stream_id=r.stream_id AND v.run_id=r.id JOIN streams s ON s.id=r.stream_id WHERE r.stopped_at IS NULL AND s.channel_id=? GROUP BY r.stream_id,r.id', (self.records.channel_id,)).fetchall()
            for row in rows:
                end = from_rfc3339(row['last'] or row['started_at'])+timedelta(microseconds=1)
                c.execute('UPDATE collection_runs SET stopped_at=? WHERE stream_id=? AND id=?', (to_rfc3339(end), row['stream_id'], row['id']))
            c.execute("UPDATE follower_sync_runs SET state='failed',finished_at=? WHERE channel_id=? AND state='collecting'",
                      (to_rfc3339(self.records.clock()), self.records.channel_id))
        self.run_id = uuid4().hex

    def stopped(self):
        now = to_rfc3339(self.records.clock())
        with self.records.transaction(write=True) as c:
            c.execute('UPDATE collection_runs SET stopped_at=? WHERE id=? AND stopped_at IS NULL AND started_at<=?', (now, self.run_id, now))

    def save(self, stream, channel, at):
        encoded_at = to_rfc3339(at)
        channel_id = self.records.channel_id
        if not isinstance(channel, dict) or channel.get('broadcaster_id') != channel_id or not isinstance(channel.get('title'), str) or len(channel['title']) > 200:
            raise PersistenceError('invalid_channel_observation', 'recording')
        tags = channel.get('tags')
        if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(t, str) or len(t) > 100 for t in tags):
            raise PersistenceError('invalid_channel_observation', 'recording')
        game_id, game_name = channel.get('game_id') or None, channel.get('game_name') or None
        if any(v is not None and (not isinstance(v, str) or len(v) > 200) for v in (game_id, game_name)):
            raise PersistenceError('invalid_channel_observation', 'recording')
        if stream is not None:
            try:
                if stream['user_id'] != channel_id:
                    raise ValueError
                started = from_rfc3339(stream['started_at'])
                snapshot = StreamSnapshot(state='live', id=stream['id'], title=stream['title'],
                    game=stream.get('game_name') or None, viewer_count=stream['viewer_count'],
                    started_at=started, observed_at=at)
                if snapshot.viewer_count is None or started > at:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise PersistenceError('invalid_stream_observation', 'recording') from None
        else:
            snapshot = StreamSnapshot(state='offline', observed_at=at)
        with self.records.transaction(write=True) as c:
            # Revision tracks changes in editable values, not identical polling.
            values = (channel['title'], game_id, game_name, json.dumps(tags))
            old = c.execute('SELECT title,game_id,game_name,tags_json,revision FROM channel_read_model WHERE channel_id=?', (channel_id,)).fetchone()
            revision = 1 if old is None else old['revision']+int(tuple(old)[:4] != values)
            c.execute("INSERT INTO channel_read_model VALUES (?,?,?,?,?,NULL,?,'helix',?) ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title,game_id=excluded.game_id,game_name=excluded.game_name,tags_json=excluded.tags_json,observed_at=excluded.observed_at,source=excluded.source,revision=excluded.revision", (channel_id, *values, encoded_at, revision))
            previous = c.execute("SELECT id,started_at FROM streams WHERE channel_id=? AND source='bot' AND ended_at IS NULL", (channel_id,)).fetchall()
            for row in previous:
                if row['id'] != snapshot.id:
                    # Helix establishes only a detection time, never an exact end time.
                    if row['started_at'] > encoded_at:
                        raise PersistenceError('invalid_timestamp', 'recording')
                    c.execute("UPDATE streams SET ended_at=?,updated_at=?,revision=revision+1 WHERE id=?", (encoded_at, encoded_at, row['id']))
                    c.execute('UPDATE collection_runs SET stopped_at=? WHERE stream_id=? AND stopped_at IS NULL', (encoded_at, row['id']))
                    last = c.execute("SELECT MAX(observed_at) FROM stream_presence WHERE channel_id=? AND stream_id=? AND state='live'", (channel_id, row['id'])).fetchone()[0]
                    precision = 'estimated' if last and (at-from_rfc3339(last)).total_seconds() <= 30 else 'unknown'
                    c.execute("INSERT INTO stream_metric_state(stream_id,end_precision) VALUES (?,?) ON CONFLICT(stream_id) DO UPDATE SET end_precision=excluded.end_precision,revision=revision+1", (row['id'], precision))
            if stream is not None:
                old_stream = c.execute('SELECT channel_id,started_at,ended_at FROM streams WHERE id=?', (snapshot.id,)).fetchone()
                start = to_rfc3339(started)
                if old_stream and (old_stream['channel_id'] != channel_id or old_stream['started_at'] != start or old_stream['ended_at'] is not None):
                    raise PersistenceError('stream_identity_conflict', 'recording')
                c.execute("INSERT INTO streams(id,channel_id,title,game_id,game_name,tags_json,started_at,source,completeness,legacy_metadata_json,created_at,updated_at,revision) VALUES (?,?,?,?,?,?,?,'bot','partial','{}',?,?,1) ON CONFLICT(id) DO UPDATE SET title=excluded.title,game_id=excluded.game_id,game_name=excluded.game_name,tags_json=excluded.tags_json,updated_at=excluded.updated_at,revision=revision+1", (snapshot.id, channel_id, snapshot.title, game_id, snapshot.game, json.dumps(tags), start, encoded_at, encoded_at))
                c.execute('INSERT OR IGNORE INTO collection_runs VALUES (?,?,?,NULL)', (snapshot.id, self.run_id, encoded_at))
                c.execute('INSERT INTO viewer_observations VALUES (?,?,?,?)', (snapshot.id, self.run_id, encoded_at, snapshot.viewer_count))
                c.execute('UPDATE observation_gaps SET ended_at=? WHERE stream_id=? AND ended_at IS NULL AND started_at<?', (encoded_at, snapshot.id, encoded_at))
            c.execute('INSERT INTO stream_presence VALUES (?,?,?,?)', (channel_id, encoded_at, snapshot.state, snapshot.id))
        return snapshot

    def gap(self, stream_id, at, reason='request_failed'):
        if not stream_id:
            return
        with self.records.transaction(write=True) as c:
            if not c.execute('SELECT 1 FROM observation_gaps WHERE stream_id=? AND ended_at IS NULL', (stream_id,)).fetchone():
                c.execute('INSERT INTO observation_gaps VALUES (?,?,?,NULL,?)', (stream_id, uuid4().hex, to_rfc3339(at), reason))
