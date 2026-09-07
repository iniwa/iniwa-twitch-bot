import json
import pytest

from tools.serve_v2_login_setup import create_app, resume_app
from twitchbot.adapters.twitch import HttpReply

CONFIDENTIAL={'client_id':'newfixture','client_type':'confidential','client_secret':'syntheticsecret'}


def test_setup_is_loopback_only_verifies_app_and_never_returns_secret(tmp_path):
    calls=[]
    def fake(method,url,**kwargs):
        calls.append((method,url))
        return HttpReply(200,{'access_token':'synthetic-app-token'})
    app=create_app({'client_id':'fixture','broadcaster_id':'123'},tmp_path,transport=fake)
    client=app.test_client()
    try:
        assert client.get('/').status_code==200 and calls==[]
        assert client.get('/',environ_overrides={'REMOTE_ADDR':'192.0.2.1'}).status_code==403
        assert client.get('/',headers={'Host':'attacker.test'}).status_code==403
        rejected=client.post('/api/v2/setup',json=CONFIDENTIAL)
        assert rejected.status_code==403 and rejected.json['error']=='origin_rejected'
        assert client.get('/api/v2/setup').json=={'configured':False,'error':'origin_rejected'}
        response=client.post('/api/v2/setup',json=CONFIDENTIAL,headers={'Origin':'http://localhost'})
        assert response.status_code==200 and response.get_json()=={'state':'configured'}
        assert len(calls)==1
        assert 'syntheticsecret' not in client.get('/api/v2/login').get_data(as_text=True)
        assert client.get('/api/v2/operations').get_json()['enabled'] is False
        assert (tmp_path/'private/runtime.json').exists()
        assert client.post('/api/v2/setup',json=CONFIDENTIAL,headers={'Origin':'http://localhost'}).status_code==409
    finally:app.extensions['login_setup.close']()


def test_setup_rejects_bad_secret_without_saving_it(tmp_path):
    app=create_app({'client_id':'fixture','broadcaster_id':'123'},tmp_path,transport=lambda *a,**k:HttpReply(403,{'message':'private failure'}))
    try:
        response=app.test_client().post('/api/v2/setup',json=CONFIDENTIAL,headers={'Origin':'http://localhost'})
        assert response.status_code==400
        assert response.json=={'error':'application_verification_failed'}
        assert not (tmp_path/'private/oauth-application.json').exists()
        assert 'private failure' not in response.get_data(as_text=True)
    finally:app.extensions['login_setup.close']()


def test_new_public_app_does_not_reuse_old_client_or_bot_or_request_app_token(tmp_path):
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(args)
        raise AssertionError('No Twitch calls before explicit login')
    app=create_app({'client_id':'oldfixture','broadcaster_id':'123','bot_user_id':'456'},tmp_path,transport=forbidden)
    try:
        response=app.test_client().post('/api/v2/setup',json={'client_id':'newfixture','client_type':'public'},headers={'Origin':'http://localhost'})
        assert response.status_code==200
        saved=json.loads((tmp_path/'private/oauth-application.json').read_text())
        assert saved['accounts']=={'broadcaster':{'client_id':'newfixture','user_id':'123','client_type':'public'}}
        assert app.test_client().get('/api/v2/operations').json['enabled'] is False
        assert app.test_client().get('/api/v2/setup').json=={'configured':True,'error':None}
        assert calls==[]
    finally:app.extensions['login_setup.close']()


@pytest.mark.parametrize('body',[
    {}, {'client_id':'newfixture','client_type':'confidential'},
    {'client_id':'newfixture','client_type':'public','client_secret':'shouldnotbesaved'},
    {'client_id':'invalid/id','client_type':'public'},
])
def test_invalid_application_does_not_create_database(tmp_path,body):
    app=create_app({'client_id':'fixture','broadcaster_id':'123'},tmp_path)
    try:
        response=app.test_client().post('/api/v2/setup',json=body,headers={'Origin':'http://localhost'})
        assert response.status_code==400 and response.json=={'error':'invalid_application'}
        assert not (tmp_path/'candidate.sqlite3').exists()
    finally:app.extensions['login_setup.close']()


def test_transport_failure_is_safe_and_retryable(tmp_path):
    attempts=[]
    def fake(*args,**kwargs):
        attempts.append(1)
        if len(attempts)==1: raise RuntimeError('syntheticsecret private exception')
        return HttpReply(200,{'access_token':'synthetic-app-token'})
    app=create_app({'client_id':'fixture','broadcaster_id':'123'},tmp_path,transport=fake)
    try:
        client=app.test_client()
        failed=client.post('/api/v2/setup',json=CONFIDENTIAL,headers={'Origin':'http://localhost'})
        assert failed.status_code==503 and failed.json=={'error':'oauth_unavailable'}
        assert not (tmp_path/'private/oauth-application.json').exists()
        assert client.post('/api/v2/setup',json=CONFIDENTIAL,headers={'Origin':'http://localhost'}).status_code==200
        assert client.get('/api/v2/setup').json['error'] is None
    finally:app.extensions['login_setup.close']()


def test_save_failure_is_safe_and_removes_created_configuration(tmp_path,monkeypatch):
    from tools import serve_v2_login_setup as setup
    original=setup.write_private
    def fail_second(path,value):
        if path.name=='runtime.json': raise OSError('private local path')
        original(path,value)
    monkeypatch.setattr(setup,'write_private',fail_second)
    app=create_app({'client_id':'fixture','broadcaster_id':'123'},tmp_path)
    try:
        response=app.test_client().post('/api/v2/setup',json={'client_id':'newfixture','client_type':'public'},headers={'Origin':'http://localhost'})
        assert response.status_code==500 and response.json=={'error':'setup_failed'}
        assert not (tmp_path/'private/oauth-application.json').exists()
        assert app.test_client().get('/api/v2/login').json['configured'] is False
    finally:app.extensions['login_setup.close']()


def test_resume_keeps_saved_grant_and_disabled_recording_without_network(tmp_path,monkeypatch):
    import time
    from twitchbot.adapters.oauth import PrivateGrantStore
    from twitchbot.application.login import BASE_SCOPES
    from twitchbot.application.persistence import PersistenceError
    from twitchbot.adapters.twitch import RequestsTransport
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(True)
        raise AssertionError('No network on paused restart')
    monkeypatch.setattr(RequestsTransport,'__call__',forbidden)
    app=create_app({'client_id':'fixture','broadcaster_id':'123'},tmp_path)
    assert app.test_client().post('/api/v2/setup',json={'client_id':'newfixture','client_type':'public'},headers={'Origin':'http://localhost'}).status_code==200
    try:
        with pytest.raises(PersistenceError,match='runtime_already_owned'):
            resume_app(tmp_path)
    finally:app.extensions['login_setup.close']()
    store=PrivateGrantStore(tmp_path/'private/grants')
    store.save('broadcaster',{'grant':{'client_id':'newfixture','user_id':'123','access_token':'syntheticaccess','refresh_token':'syntheticrefresh','expires_at':time.time()+3600,'requested_scopes':list(BASE_SCOPES)},'candidate':None,'refresh_pending':False})
    before=store.path('broadcaster').read_bytes()
    app=resume_app(tmp_path)
    try:
        client=app.test_client()
        assert client.get('/').location=='/v2/connect'
        assert client.get('/api/v2/login').json['accounts']['broadcaster']=={'state':'saved'}
        assert client.get('/api/v2/operations').json['enabled'] is False
        assert client.post('/api/v2/setup',json=CONFIDENTIAL,headers={'Origin':'http://localhost'}).status_code==405
        assert client.get('/',headers={'Host':'attacker.test'}).status_code==403
        assert client.get('/',environ_overrides={'REMOTE_ADDR':'192.0.2.1'}).status_code==403
        assert client.get('/api/v2/login').headers['Cache-Control']=='no-store'
        assert store.path('broadcaster').read_bytes()==before
        assert calls==[]
    finally:app.extensions['login_setup.close']()


def test_resume_failure_releases_ownership_without_overwriting_config(tmp_path):
    from twitchbot.application.workers import ProcessLease
    with pytest.raises(Exception): resume_app(tmp_path)
    lease=ProcessLease(tmp_path/'.setup.lock')
    lease.acquire();lease.release()
    assert not (tmp_path/'private/runtime.json').exists()
