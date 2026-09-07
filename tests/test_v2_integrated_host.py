"""The candidate host keeps legacy routes while construction stays inert."""

from pathlib import Path
import re

import pytest

from routes.application import create_app
from src.twitchbot.container import Container


def test_factory_does_not_read_config_or_start_either_runtime(monkeypatch):
    import config
    import services.workers as workers
    from src.twitchbot.runtime import RuntimeSupervisor
    def forbidden(*args, **kwargs):
        pytest.fail('Application construction performed runtime IO')
    monkeypatch.setattr(config, 'load_config', forbidden)
    monkeypatch.setattr(workers, 'start_workers', forbidden)
    monkeypatch.setattr(RuntimeSupervisor, 'start', forbidden)
    legacy = create_app()
    candidate = create_app(v2_container=Container())
    assert legacy.test_client().get('/api/stream/status').status_code == 200
    assert candidate.test_client().get('/api/v2/operations').status_code == 503
    assert candidate.extensions['twitchbot.container'].runtime.snapshot().ready is False


def test_candidate_retains_every_legacy_route_and_static_boundary():
    legacy = create_app()
    candidate = create_app(v2_container=Container())
    def rules(app):
        return {(r.rule, r.endpoint, tuple(sorted(r.methods))) for r in app.url_map.iter_rules()}
    assert rules(legacy) <= rules(candidate)
    client = candidate.test_client()
    assert client.get('/api/download_progress').status_code == 200
    assert client.get('/v2-static/v2/control.css').status_code == 200
    assert client.get('/static/v2/control.css').status_code == 404
    assert candidate.root_path == str(Path(__file__).resolve().parent.parent)


def test_candidate_status_uses_detached_legacy_cache(monkeypatch):
    import config
    import services.twitch_api as twitch
    def forbidden(*args, **kwargs): pytest.fail('Status called Twitch or configuration')
    monkeypatch.setattr(twitch, 'check_stream_status_and_update', forbidden)
    monkeypatch.setattr(config, 'load_config', forbidden)
    config.set_current_stream({'id':'fixture','title':'saved','game_name':'game','started_at':'2026-09-07T00:00:00Z','channel_name':'fixture'})
    try:
        client = create_app(v2_container=Container()).test_client()
        result = client.get('/api/stream/status').json
        assert result['live'] and result['stream']['title'] == 'saved'
        result['stream']['title'] = 'client mutation'
        assert client.get('/api/stream/status').json['stream']['title'] == 'saved'
    finally: config.clear_current_stream()


def test_legacy_analytics_and_vod_actions_still_resolve(monkeypatch):
    import config
    import routes.analytics as analytics
    import routes.vod as vod
    monkeypatch.setattr(config, 'load_config', lambda: {})
    monkeypatch.setattr(config, 'load_viewers', lambda: {})
    monkeypatch.setattr(analytics, 'load_stream_index', lambda: {})
    cancelled=[]
    monkeypatch.setattr(vod, 'request_cancel_download', cancelled.append)
    client = create_app(v2_container=Container()).test_client()
    assert client.get('/analytics').status_code == 200
    result = client.post('/cancel_download/fixture')
    assert result.status_code == 302 and result.location.endswith('/analytics')
    assert cancelled == ['fixture']
    result = client.post('/cancel_download/..')
    assert result.status_code == 302 and cancelled == ['fixture']


def test_new_pages_render_with_all_local_assets(tmp_path):
    from src.twitchbot.bootstrap import build_container
    from src.twitchbot.adapters.twitch import TwitchCredentials
    staging=tmp_path/'backups';staging.mkdir()
    container=build_container(tmp_path/'candidate.sqlite3', staging, TwitchCredentials('fixture','123','synthetic'), '123')
    app=create_app(v2_container=container)
    client=app.test_client()
    for url in ('/v2/control','/v2/history','/v2/settings','/v2/connect','/v2/presets','/v2/automation','/v2/predictions'):
        response=client.get(url)
        assert response.status_code == 200, url
        for asset in re.findall(r'(?:src|href)="(/v2-static/[^\"]+)"', response.text):
            assert client.get(asset).status_code == 200, asset
    assert not container.runtime.snapshot().ready


def test_container_cannot_be_replaced_after_registration():
    from src.twitchbot.web.app import register_blueprints
    app=create_app(v2_container=Container())
    with pytest.raises(ValueError,match='already registered'):
        register_blueprints(app,Container())
