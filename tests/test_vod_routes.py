"""VOD ルートと設定に OBS / 管理者モード依存が残っていないことのテスト。"""

import pytest

pytest.importorskip('flask')  # routes.vod は flask に依存

import config as c  # noqa: E402
import routes.vod as vodmod  # noqa: E402


def test_admin_gate_helper_removed():
    assert not hasattr(vodmod, '_vod_admin_required')


def test_admin_mode_api_removed():
    assert not hasattr(c, 'is_admin_mode')
    assert not hasattr(c, 'toggle_admin_mode')


def test_default_config_has_no_obs_archive():
    assert 'obs_archive' not in c.DEFAULT_CONFIG


def test_vod_routes_redirect_without_admin(client, monkeypatch):
    """manual / bulk / cancel / delete が管理者モードなしでも実行できる。"""
    # Route behavior must not read local credentials or create runtime data.
    monkeypatch.setattr(c, 'load_config', lambda: {})
    monkeypatch.setattr(vodmod, 'execute_download', lambda conf, sid: None)
    monkeypatch.setattr(vodmod, 'bulk_download_task', lambda conf: None)
    monkeypatch.setattr(vodmod, 'request_cancel_download', lambda sid: None)
    monkeypatch.setattr(vodmod, 'delete_vod_file', lambda sid: None)

    for path in (
        '/download_vod/abc123',
        '/download_all_vods',
        '/cancel_download/abc123',
        '/delete_vod/abc123',
    ):
        resp = client.post(path)
        assert resp.status_code == 302, path


def test_obs_archive_routes_absent(client):
    """削除された OBS 連携 API が 404 を返す。"""
    assert client.post('/api/obs_archive/test').status_code == 404
    assert client.get('/api/obs_archive/vod_migration/scan').status_code == 404
    assert client.post('/toggle_admin_mode').status_code == 404
