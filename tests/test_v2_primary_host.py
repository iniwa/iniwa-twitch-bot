from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.twitchbot.application.live import LiveSnapshot, StreamSnapshot
from src.twitchbot.bootstrap import build_container
from src.twitchbot.adapters.twitch import TwitchCredentials, HttpReply
from services.v2_host import create_operational_app, ArchiveWorker


@pytest.fixture
def primary(tmp_path, monkeypatch):
    import config
    import services.workers
    def forbidden(*a, **k): pytest.fail('Old workers started')
    monkeypatch.setattr(services.workers, 'start_workers', forbidden)
    monkeypatch.setattr(config, 'load_config', lambda: {'channel_name':'fixture','access_token':'old-secret','enable_vod_download':False})
    monkeypatch.setattr(config, 'load_viewers', lambda: {})
    staging=tmp_path/'backups'; staging.mkdir()
    calls=[]
    def transport(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/validate'):
            return HttpReply(200, {'client_id':'fixture','user_id':'123','scopes':[],'expires_in':4000})
        return HttpReply(200, {'data':[]})
    container=build_container(tmp_path/'main.sqlite3',staging,TwitchCredentials('fixture','123','first'),'123',transport=transport)
    app=create_operational_app(container)
    return container, app, calls


def test_primary_root_and_archived_history_without_old_worker_start(primary):
    container, app, calls=primary
    client=app.test_client()
    assert client.get('/').location.endswith('/v2/control')
    assert client.get('/legacy/viewers').status_code==200
    assert '/analytics' in client.get('/v2/history').text
    assert '/legacy/viewers' in client.get('/v2/community').text
    assert not container.runtime.snapshot().ready and not calls
    assert [w.name for w in container.runtime.workers].count('archives')==1


def test_operations_status_does_not_hold_settings_lock_while_reading_client(primary, monkeypatch):
    from threading import Thread
    container, _, _ = primary
    results=[]
    def status():
        worker=Thread(target=lambda:results.append(container.operations.allowed()),daemon=True)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive(), 'status blocks the network worker dispatch gate'
        return {'state':'not_validated'}
    monkeypatch.setattr(container.operations.client,'status',status)
    assert container.operations.snapshot()['connection']['state']=='not_validated'
    assert results==[False]


def test_external_status_follows_new_cached_stream_and_is_detached(primary, monkeypatch):
    container, app, calls=primary
    import config
    monkeypatch.setattr(config,'load_config',lambda:pytest.fail('GET read config'))
    stream=StreamSnapshot(state='live',id='42',title='live title',game='game',started_at='2026-09-07T00:00:00Z')
    container.live_provider._snapshot=LiveSnapshot(stream=stream)
    client=app.test_client()
    reply=client.get('/api/stream/status').json
    assert reply['live'] and reply['stream']['channel_name']=='fixture'
    reply['stream']['title']='mutation'
    assert client.get('/api/stream/status').json['stream']['title']=='live title'
    container.live_provider._snapshot=LiveSnapshot(stream=replace(stream,state='degraded',stale=True))
    assert client.get('/api/stream/status').json['live']
    container.live_provider.stopped()
    assert client.get('/api/stream/status').json['stream'] is None
    assert calls==[]


def test_vod_callback_uses_rotated_grant_and_closed_gate(primary):
    container, app, calls=primary
    client=container.operations.client
    # Explicit fake runtime ownership: no threads/network except fake transport.
    client.allowed=lambda:True
    conf=app.extensions['twitchbot.vod_configuration']()
    assert set(conf)=={'broadcaster_id','enable_vod_download','_videos_reader'}
    client.validate()
    assert conf['_videos_reader'](20)==[]
    assert calls[-1][2]['headers']['Authorization']=='Bearer first'
    client.replace_credentials(TwitchCredentials('fixture','123','second'))
    client.validate()
    conf['_videos_reader'](100)
    assert calls[-1][2]['headers']['Authorization']=='Bearer second'
    client.allowed=lambda:False
    from src.twitchbot.adapters.twitch import TwitchFailure
    before=len(calls)
    with pytest.raises(TwitchFailure,match='runtime_stopped'): conf['_videos_reader'](20)
    assert len(calls)==before


def test_archive_auto_download_waits_and_never_starts_when_disabled(monkeypatch):
    import services.v2_host as host
    index={}; calls=[]; enabled=[True]; now=[1000]
    monkeypatch.setattr(host,'load_stream_index',lambda:index)
    monkeypatch.setattr(host,'save_stream_index',lambda value:None)
    monkeypatch.setattr(host,'sync_vod_history',lambda conf:calls.append('sync'))
    monkeypatch.setattr(host,'execute_download',lambda conf,sid:calls.append(sid))
    live=[LiveSnapshot(stream=StreamSnapshot(state='live',id='42',title='title'))]
    container=SimpleNamespace(operations=SimpleNamespace(allowed=lambda:True,client=SimpleNamespace(status=lambda:{'read':True})),live_provider=SimpleNamespace(snapshot=lambda:live[0]))
    worker=ArchiveWorker(container,lambda:{'enable_vod_download':enabled[0]},clock=lambda:now[0])
    worker.step()
    assert index['42']['source']=='v2'
    live[0]=LiveSnapshot(stream=StreamSnapshot(state='degraded',id='42',stale=True)); worker.step()
    assert not worker.pending
    live[0]=LiveSnapshot(stream=StreamSnapshot(state='offline')); worker.step()
    assert calls==['sync'] and worker.pending
    now[0]+=301; enabled[0]=False; worker.step()
    assert calls==['sync'] and not worker.pending
