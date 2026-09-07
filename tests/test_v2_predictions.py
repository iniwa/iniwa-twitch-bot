from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from twitchbot.adapters.persistence import SQLiteDatabase
from twitchbot.adapters.persistence.recording import RecordingRepository
from twitchbot.adapters.twitch import TwitchFailure
from twitchbot.application.live import LiveSnapshot, StreamSnapshot
from twitchbot.application.predictions import Predictions
from twitchbot.application.persistence import PersistenceError

SPEC = dict(title='クリアできる？', outcomes=['はい', 'いいえ'], prediction_window=120)
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


@pytest.fixture
def predictions(tmp_path):
    db = SQLiteDatabase(tmp_path/'core.sqlite3'); db.migrate()
    clock = [NOW]
    live = SimpleNamespace(value=LiveSnapshot(stream=StreamSnapshot(state='live', id='s1', title='Stream', observed_at=NOW, started_at=NOW)))
    live.snapshot = lambda: live.value
    recording = RecordingRepository(db, '123', clock=lambda: clock[0])
    recording.save({'id': 's1', 'user_id': '123', 'title': 'Stream', 'started_at': NOW.isoformat(), 'viewer_count': 5}, {'broadcaster_id': '123', 'title': 'Stream', 'game_id': '', 'game_name': '', 'tags': []}, NOW)
    class Client:
        items = []
        calls = []
        failure = None
        read_failure = False
        def validate(self): pass
        def predictions(self):
            if self.read_failure: raise TwitchFailure('transport_failed')
            return list(self.items)
        def change_prediction(self, action, payload):
            self.calls.append(action)
            if self.failure: raise self.failure
            if action == 'start':
                item = dict(id='p1', title=payload['title'], status='ACTIVE', outcomes=[dict(id=str(i), title=t) for i,t in enumerate(payload['outcomes'])], winning_outcome_id=None)
                self.items = [item]
            else:
                item = dict(self.items[0], status={'lock':'LOCKED','resolve':'RESOLVED','cancel':'CANCELED'}[action], winning_outcome_id=payload.get('winning_outcome_id'))
                self.items = [item]
            return item
    client = Client()
    service = Predictions(db, '123', client, live, running=lambda: True, clock=lambda: clock[0])
    service.save_preset('preset', 'いつもの予想', SPEC, 0)
    return service, client, live, clock


def start(service):
    service.save_policy(True, 0); service.step()
    preview = service.preview('start', 'preset')
    service.confirm(preview['id']); service.step()
    return preview


def test_defaults_off_preview_no_write_and_deduplicated_confirmation(predictions):
    service, client, _, _ = predictions
    assert service.snapshot()['policy']['enabled'] is False
    service.step()
    with pytest.raises(PersistenceError): service.preview('start', 'preset')
    service.save_policy(True, 0)
    preview = service.preview('start', 'preset')
    assert not client.calls
    service.confirm(preview['id']); service.confirm(preview['id']); service.step(); service.confirm(preview['id'])
    assert client.calls == ['start']
    with pytest.raises(PersistenceError, match='prediction_already_active'): service.preview('start', 'preset')


def test_preset_display_sort_uses_saved_timestamps(predictions):
    service, _, _, clock = predictions
    clock[0] += timedelta(minutes=1)
    second = service.save_preset('second', 'alpha', SPEC, 0)
    clock[0] += timedelta(minutes=1)
    changed = service.save_preset('preset', 'Ｂeta', SPEC, 1)
    assert changed['created_at'] < changed['updated_at'] and second['created_at'] == second['updated_at']
    assert [item['id'] for item in service.snapshot(sort='name')['presets']] == ['second', 'preset']
    assert [item['id'] for item in service.snapshot(sort='updated_at', order='desc')['presets']] == ['preset', 'second']
    with pytest.raises(PersistenceError, match='invalid_sort'):
        service.snapshot(sort='updated_at DESC')


def test_offline_and_feature_off_keep_manual_finish(predictions):
    service, client, live, clock = predictions
    start(service)
    service.save_policy(False, 1)
    live.value = LiveSnapshot(stream=StreamSnapshot(state='offline', observed_at=NOW))
    with pytest.raises(PersistenceError): service.preview('resolve', 'p1', '0')
    preview = service.preview('lock', 'p1'); service.confirm(preview['id']); service.step()
    preview = service.preview('resolve', 'p1', '0')
    assert preview['content']['winning_title'] == 'はい'
    service.confirm(preview['id']); service.step()
    assert client.calls == ['start', 'lock', 'resolve']
    with service.repository.transaction() as c:
        assert c.execute("SELECT COUNT(*) FROM channel_events WHERE stream_id='s1'").fetchone()[0] == 3


def test_unknown_start_not_retried_or_inferred_from_title(predictions):
    service, client, _, _ = predictions
    client.failure = TwitchFailure('transport_failed', uncertain=True)
    start(service); service.step()
    assert service.snapshot()['operations'][0]['state'] == 'unknown'
    with pytest.raises(PersistenceError, match='prediction_already_active_or_unknown'): service.preview('start', 'preset')
    assert client.calls == ['start']


def test_edit_after_preview_and_external_state_change_block_dispatch(predictions):
    service, client, _, _ = predictions
    service.save_policy(True, 0); service.step()
    preview = service.preview('start', 'preset')
    service.save_preset('preset', 'Changed', dict(SPEC, title='別の内容'), 1)
    service.confirm(preview['id']); service.step()
    assert not client.calls
    assert service.snapshot()['operations'][0]['result_code'] == 'preview_changed'


def test_failed_poll_preserves_previous_prediction_and_restart_no_replay(predictions):
    service, client, _, _ = predictions
    start(service)
    preview = service.preview('cancel', 'p1'); service.confirm(preview['id'])
    service.recover(); service.step()
    assert client.calls == ['start']
    client.read_failure = True; service.step()
    assert not service.snapshot()['fresh']
    assert service.snapshot()['items'][0]['status'] == 'ACTIVE'
    with pytest.raises(PersistenceError): service.preview('cancel', 'p1')


def test_unknown_known_id_reconciles_without_resending(predictions):
    service, client, _, _ = predictions
    start(service)
    preview = service.preview('lock', 'p1'); service.confirm(preview['id'])
    client.failure = TwitchFailure('transport_failed', uncertain=True)
    service.step()
    result = lambda: next(o for o in service.snapshot()['operations'] if o['id'] == preview['id'])
    assert result()['state'] == 'unknown'
    client.items[0]['status'] = 'LOCKED'
    service.step()
    assert result()['result_code'] == 'state_confirmed_on_refresh'
    assert client.calls == ['start', 'lock']
