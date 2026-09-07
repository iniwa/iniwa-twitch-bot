import json
from pathlib import Path
import pytest

from twitchbot.adapters.persistence.backups import BackupLimits
from twitchbot.adapters.twitch import TwitchCredentials
from twitchbot.bootstrap import build_container
from twitchbot.web.app import create_app


@pytest.fixture
def operational(tmp_path):
    staging = tmp_path/'snapshots'; staging.mkdir()
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        pytest.fail('unexpected external request')
    container = build_container(tmp_path/'core.sqlite3', staging,
        TwitchCredentials('fixture', '123', 'fixture-token'), '123', transport=forbidden,
        backup_limits=BackupLimits(reserve_bytes=0, reserve_fraction=0))
    return container, create_app(container).test_client(), calls


def test_operational_factory_and_get_do_not_start_or_connect(operational):
    container, client, calls = operational
    assert not container.runtime.snapshot().ready
    page = client.get('/v2/settings')
    assert page.status_code == 200
    assert '<main data-page="settings">' in page.text and '/v2-static/v2/app.js' in page.text
    assert 'backup-list-controls' in page.text and 'backup-more' in page.text
    state = client.get('/api/v2/operations').get_json()
    assert state['enabled'] is False and state['backups']['items'] == []
    assert calls == []
    assert 'fixture-token' not in str(state)


def test_backup_list_sort_filter_and_cursor_are_read_only(operational):
    container, client, calls = operational
    root = container.operations.backup_worker.service.root
    manifests = [
        {'id': 'a'*32, 'state': 'local_ready', 'size_bytes': 20,
         'created_at': '2026-09-01T00:00:00Z', 'copy_completed_at': '2026-09-01T00:00:01Z'},
        {'id': 'b'*32, 'state': 'nas_verified', 'size_bytes': 10,
         'created_at': '2026-09-02T00:00:00Z', 'copy_completed_at': '2026-09-02T00:00:01Z',
         'nas_verified_at': '2026-09-02T00:00:02Z'},
    ]
    for item in manifests:
        folder = root/item['id']; folder.mkdir()
        payload = {**item, 'reasons': ['manual'], 'checksum': '0'*64, 'counts': {}}
        payload.setdefault('nas_verified_at', None)
        (folder/'manifest.json').write_text(json.dumps(payload), encoding='utf-8')
    first = client.get('/api/v2/backups?sort=size_bytes&order=asc&limit=1').get_json()
    assert [item['id'] for item in first['items']] == ['b'*32]
    assert first['next_cursor'] and first['sort'] == 'size_bytes' and first['order'] == 'asc'
    second = client.get('/api/v2/backups', query_string={'sort': 'size_bytes', 'order': 'asc',
                        'limit': 1, 'cursor': first['next_cursor']}).get_json()
    assert [item['id'] for item in second['items']] == ['a'*32]
    filtered = client.get('/api/v2/backups?state=local_ready').get_json()
    assert [item['id'] for item in filtered['items']] == ['a'*32]
    assert client.get('/api/v2/backups?sort=created_at%20DESC').status_code == 400
    assert client.get('/api/v2/backups?order=asc&order=desc').status_code == 400
    assert client.get('/api/v2/backups?cursor=' + 'a'*1025).status_code == 400
    assert client.get('/api/v2/backups', query_string={'sort': 'created_at', 'order': 'asc',
                      'state': 'local_ready', 'cursor': first['next_cursor']}).status_code == 400
    assert calls == []


def test_backup_policy_revisions_and_origin_guard(operational):
    container, client, calls = operational
    body = {'enabled': True, 'daily_hour': 7, 'revision': 0}
    assert client.post('/api/v2/backup-policy', json=body).status_code == 403
    good = client.post('/api/v2/backup-policy', json=body, headers={'Origin': 'http://localhost'})
    assert good.status_code == 200 and good.get_json()['daily_hour'] == 7
    assert client.post('/api/v2/backup-policy', json=body, headers={'Origin': 'http://localhost'}).status_code == 409
    assert calls == []


def test_manual_backup_is_durable_deduplicated_and_worker_owned(operational):
    container, client, calls = operational
    body = {'request_id': 'manual-request'}
    for _ in range(2):
        assert client.post('/api/v2/backups', json=body, headers={'Origin': 'http://localhost'}).status_code == 202
    worker = container.operations.backup_worker
    assert len(container.operations.maintenance.jobs()) == 1
    assert worker.service.list_backups() == []
    worker.running = lambda: True
    worker.step()
    assert len(worker.service.list_backups()) == 1
    assert container.operations.maintenance.jobs()[0]['state'] == 'succeeded'
    assert calls == []


def test_restore_disables_backup_policy_and_pending_requests(operational, tmp_path):
    container, _, _ = operational
    operations = container.operations
    operations.maintenance.save_policy(True, 4, 0)
    operations.maintenance.enqueue('before-restore')
    service = operations.backup_worker.service
    item = service.create()
    result = service.prepare_restore(item['id'], tmp_path/'restored.sqlite3')
    assert result['automatic_execution'] == 'stopped'
    from twitchbot.adapters.persistence import SQLiteDatabase
    from twitchbot.adapters.persistence.maintenance import MaintenanceRepository
    restored = MaintenanceRepository(SQLiteDatabase(tmp_path/'restored.sqlite3'), '123')
    assert restored.policy()['enabled'] is False
    assert restored.jobs()[0]['state'] == 'unknown'


def test_policy_daily_hour_changes_due_day(operational):
    from datetime import datetime, timezone
    from twitchbot.application.backups import daily_backup_due
    at = datetime(2026, 9, 5, 21, tzinfo=timezone.utc)  # JST 06:00
    assert daily_backup_due(at, '2026-09-05', running=True, hour=4) == '2026-09-06'
    assert daily_backup_due(at, '2026-09-05', running=True, hour=7) is None


def test_automation_and_predictions_management_are_inert(operational):
    from test_v2_automation import COMMAND
    from test_v2_predictions import SPEC
    _, client, calls = operational
    for path in ('/v2/automation', '/v2/predictions', '/api/v2/automation', '/api/v2/predictions'):
        response = client.get(path)
        assert response.status_code == 200
        if path == '/v2/automation':
            assert '<main data-page="automation">' in response.text and '/v2-static/v2/app.js' in response.text
        if path == '/v2/predictions':
            assert '<main data-page="predictions">' in response.text and '/v2-static/v2/app.js' in response.text
    headers = {'Origin': 'http://localhost'}
    assert client.post('/api/v2/automation/definition', json=dict(id='d1', kind='command', name='Name', enabled=False, specification=COMMAND, revision=0), headers=headers).status_code == 200
    preview = client.post('/api/v2/automation/preview', json=dict(specification=COMMAND, input='!sns', role='everyone'), headers=headers)
    assert preview.status_code == 200 and preview.get_json()['sent'] is False
    assert client.post('/api/v2/predictions/preset', json=dict(id='p1', name='Name', specification=SPEC, revision=0), headers=headers).status_code == 200
    assert client.post('/api/v2/predictions/policy', json=dict(enabled=True, revision=0)).status_code == 403
    assert client.get('/api/v2/automation?sort=updated_at&order=desc&enabled=disabled').status_code == 200
    assert client.get('/api/v2/automation?sort=name%20DESC').status_code == 400
    assert client.get('/api/v2/automation?enabled=yes').status_code == 400
    assert client.get('/api/v2/predictions?sort=updated_at&order=desc').status_code == 200
    assert client.get('/api/v2/predictions?order=asc&order=desc').status_code == 400
    assert calls == []


def test_restore_request_is_durable_and_creates_only_disabled_candidate(operational):
    from twitchbot.adapters.persistence import SQLiteDatabase, SettingsRepository
    container, client, calls = operational
    worker = container.operations.backup_worker
    item = worker.service.create()
    body = dict(request_id='restore-one', backup_id=item['id'])
    for _ in range(2):
        assert client.post('/api/v2/restore-candidates', json=body, headers={'Origin':'http://localhost'}).status_code == 202
    assert len(container.operations.maintenance.restore_jobs()) == 1
    assert not (worker.service.database.path.parent/'restore-candidates').exists()
    worker.running = lambda: True; worker.step()
    job = container.operations.maintenance.restore_jobs()[0]
    assert job['state'] == 'verified'
    candidate = worker.service.database.path.parent/'restore-candidates'/job['candidate_name']
    assert not SettingsRepository(SQLiteDatabase(candidate)).load().settings.bot_enabled
    assert worker.service.database.path.exists() and not calls
