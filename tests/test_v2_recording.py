from datetime import datetime, timedelta, timezone
from threading import Event
import pytest

from twitchbot.adapters.persistence import SQLiteDatabase
from twitchbot.adapters.persistence.analytics import HistoryReader
from twitchbot.adapters.persistence.recording import RecordingRepository
from twitchbot.application.recorder import Recorder, FollowerSynchronizer
from twitchbot.application.workers import ProcessLease, PeriodicWorker, WorkerRuntime
from twitchbot.application.persistence import PersistenceError
from twitchbot.adapters.twitch import TwitchFailure

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


@pytest.fixture
def recording(tmp_path):
    db = SQLiteDatabase(tmp_path/'recording.sqlite3'); db.migrate()
    clock = [NOW]
    repo = RecordingRepository(db, '123', clock=lambda: clock[0])
    channel = {'broadcaster_id': '123', 'title': 'Channel', 'game_id': '1', 'game_name': 'Game', 'tags': ['日本語']}
    stream = {'user_id': '123', 'id': 's1', 'title': 'Stream', 'game_name': 'Game',
              'viewer_count': 10, 'started_at': (NOW-timedelta(seconds=20)).isoformat()}
    return db, repo, clock, channel, stream


def test_poll_commits_observation_and_only_changes_edit_revision_on_value_change(recording):
    db, repo, clock, channel, stream = recording
    repo.save(stream, channel, clock[0])
    clock[0] += timedelta(seconds=20)
    repo.save(dict(stream, viewer_count=20), channel, clock[0])
    with db.connection() as c:
        assert c.execute('SELECT revision FROM channel_read_model').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM viewer_observations').fetchone()[0] == 2
    result = HistoryReader(db, clock=lambda: clock[0]+timedelta(microseconds=1)).detail('s1')
    assert result['max_viewers'] == 20
    clock[0] += timedelta(seconds=20)
    repo.save(None, channel, clock[0])
    with db.connection() as c:
        assert c.execute('SELECT ended_at FROM streams').fetchone()[0]
        assert c.execute('SELECT end_precision FROM stream_metric_state').fetchone()[0] == 'estimated'


def test_recording_failure_rolls_back_channel_and_stream_writes(recording):
    db, repo, clock, channel, stream = recording
    repo.save(stream, channel, clock[0])
    with pytest.raises(PersistenceError):
        repo.save(dict(stream, started_at=(NOW-timedelta(hours=1)).isoformat()), dict(channel, title='Changed'), clock[0])
    with db.connection() as c:
        assert c.execute('SELECT title FROM channel_read_model').fetchone()[0] == 'Channel'


def test_restart_does_not_bridge_downtime(recording):
    db, repo, clock, channel, stream = recording
    repo.save(stream, channel, clock[0])
    clock[0] += timedelta(hours=2)
    restarted = RecordingRepository(db, '123', clock=lambda: clock[0])
    restarted.recover()
    restarted.save(stream, channel, clock[0])
    with db.connection() as c:
        runs = c.execute('SELECT * FROM collection_runs ORDER BY started_at').fetchall()
        assert len(runs) == 2
        assert runs[0]['stopped_at'].startswith('2026-09-06T00:00:00.000001')


def test_offline_after_unobserved_downtime_has_unknown_end_precision(recording):
    db, repo, clock, channel, stream = recording
    repo.save(stream, channel, clock[0]); clock[0] += timedelta(hours=2)
    repo.recover(); repo.save(None, channel, clock[0])
    with db.connection() as c:
        assert c.execute('SELECT end_precision FROM stream_metric_state').fetchone()[0] == 'unknown'


def test_recorder_failure_preserves_stream_identity_but_marks_stale(recording):
    db, repo, clock, channel, stream = recording
    class Client:
        failed = False
        def validate(self): pass
        def stream(self):
            if self.failed: raise TwitchFailure('transport_failed')
            return stream
        def channel(self): return channel
    client = Client()
    recorder = Recorder(client, repo, clock=lambda: clock[0], running=lambda: True)
    assert recorder.step()['state'] == 'live'
    assert recorder.snapshot().connections['twitch'] == 'healthy'
    client.failed = True; clock[0] += timedelta(seconds=20)
    assert recorder.step()['state'] == 'degraded'
    assert recorder.snapshot().stream.id == 's1' and recorder.snapshot().stream.stale
    assert recorder.snapshot().connections['twitch'] == 'degraded'
    client.failed = False; clock[0] += timedelta(seconds=20)
    assert recorder.step()['state'] == 'live'
    with db.connection() as c:
        assert c.execute('SELECT ended_at FROM observation_gaps').fetchone()[0]


def test_worker_starts_once_and_lease_releases_after_stop(tmp_path):
    entered = Event()
    worker = PeriodicWorker('fixture', lambda: entered.set(), interval=100)
    path = tmp_path/'runtime.lock'
    runtime = WorkerRuntime(ProcessLease(path), [worker])
    assert not path.exists()
    runtime.start(); runtime.start()
    assert entered.wait(2)
    with pytest.raises(PersistenceError, match='runtime_already_owned'):
        ProcessLease(path).acquire()
    assert runtime.stop().state == 'stopped'
    second = ProcessLease(path); second.acquire(); second.release()
    assert worker.snapshot()['state'] == 'stopped'


def test_follower_sync_publishes_only_complete_snapshot(recording):
    db, repo, clock, channel, stream = recording
    class Client:
        def status(self): return {'followers': True}
        def followers_page(self, cursor):
            return {'total': 1, 'data': [{'user_id': 'u', 'followed_at': (NOW-timedelta(days=1)).isoformat()}], 'pagination': {}}
    sync = FollowerSynchronizer(Client(), repo.records, running=lambda: True)
    assert sync.step()['state'] == 'complete'
    assert sync.step()['state'] == 'waiting'


def test_partial_worker_start_rolls_back_without_holding_snapshot_lock(tmp_path):
    class Worker:
        def start(self): raise RuntimeError('fixture startup failed')
        def request_stop(self): pass
        def join(self):
            event = Event()
            from threading import Thread
            thread = Thread(target=lambda: (runtime.snapshot(), event.set()))
            thread.start()
            assert event.wait(1), 'snapshot blocked by lifecycle rollback'
            thread.join()
            return True
    runtime = WorkerRuntime(ProcessLease(tmp_path/'runtime.lock'), [Worker()])
    with pytest.raises(RuntimeError): runtime.start()
    lease = ProcessLease(tmp_path/'runtime.lock'); lease.acquire(); lease.release()


def test_shutdown_callback_failure_releases_owned_lease(tmp_path):
    def fail(): raise ValueError('fixture shutdown failure')
    runtime = WorkerRuntime(ProcessLease(tmp_path/'runtime.lock'), stopped=[fail])
    runtime.start()
    with pytest.raises(ValueError): runtime.stop()
    lease = ProcessLease(tmp_path/'runtime.lock'); lease.acquire(); lease.release()
