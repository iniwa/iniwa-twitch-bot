from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from twitchbot.adapters.oauth import OAuthAccount, PrivateGrantStore, TwitchOAuth
from twitchbot.adapters.twitch import HelixClient, TwitchCredentials, HttpReply, TwitchFailure
from twitchbot.application.login import BASE_SCOPES, DeviceLogin
from twitchbot.application.persistence import PersistenceError


class API:
    def __init__(self):
        self.calls=[]; self.allow=False; self.user='123'; self.fail_refresh=False
        self.scopes=list(BASE_SCOPES)

    def begin(self, account, scopes):
        self.calls.append('begin'); self.scopes=scopes
        return dict(device_code='private-device-code', user_code='ABCDEFGH', verification_uri='https://www.twitch.tv/activate?device-code=ABCDEFGH', expires_in=600, interval=5)

    def tokens(self):
        return dict(access_token='private-access', refresh_token='private-refresh', token_type='bearer', expires_in=100)

    def exchange(self, *args):
        self.calls.append('exchange')
        return HttpReply(200,self.tokens()) if self.allow else HttpReply(400,{'message':'authorization_pending'})

    def validate(self, token):
        self.calls.append('validate')
        return HttpReply(200,dict(client_id='fixture',user_id=self.user,scopes=self.scopes,expires_in=100))

    def refresh(self, *args):
        self.calls.append('refresh')
        if self.fail_refresh: raise TwitchFailure('oauth_unavailable',uncertain=True)
        return HttpReply(200,self.tokens())


@pytest.fixture
def login(tmp_path):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    store=PrivateGrantStore(private);api=API();at=[1000]
    client=HelixClient(TwitchCredentials('fixture','123','pending'), '123', transport=lambda *a,**k:pytest.fail('unexpected Helix'))
    manager=DeviceLogin({'broadcaster':OAuthAccount('fixture','123')},{'broadcaster':client},store,api,
                        owned=lambda:True,recording=lambda:True,clock=lambda:at[0])
    manager.recover()
    yield manager,api,at,client
    manager.close()


def connect(login):
    manager,api,at,_=login
    manager.begin('broadcaster');manager.step()
    api.allow=True;at[0]+=5;manager.step();manager.step()
    assert manager.snapshot()['accounts']['broadcaster']['state']=='connected'


def test_login_start_snapshot_and_cancel_are_inert(login):
    manager,api,at,client=login
    manager.begin('broadcaster');manager.begin('broadcaster')
    assert api.calls==[] and manager.snapshot()['accounts']['broadcaster']['state']=='starting'
    manager.step();at[0]+=4;manager.step()
    assert api.calls==['begin']
    snapshot=json.dumps(manager.snapshot())
    assert 'ABCDEFGH' in snapshot and 'private-device-code' not in snapshot
    manager.cancel('broadcaster');at[0]+=10;manager.step()
    assert api.calls==['begin'] and not manager.authorized('broadcaster')


def test_login_validates_identity_persists_privately_and_resumes(login):
    manager,api,at,client=login
    connect(login)
    assert client.credentials.access_token=='private-access'
    assert manager.authorized('broadcaster')
    assert all(value not in json.dumps(manager.snapshot()) for value in ('private-access','private-refresh','private-device-code'))
    path=manager.store.root/'broadcaster.json'
    if os.name=='posix': assert path.stat().st_mode & 0o077==0
    manager.close();api.calls.clear();manager.recover()
    assert api.calls==[] and manager.authorized('broadcaster')


def test_wrong_login_preserves_previously_valid_grant(login):
    manager,api,at,client=login
    connect(login);original=deepcopy(manager.records['broadcaster']['grant'])
    api.user='999';manager.begin('broadcaster');manager.step();at[0]+=5;manager.step();manager.step()
    assert manager.snapshot()['accounts']['broadcaster']['state']=='oauth_wrong_account'
    assert manager.records['broadcaster']['grant']==original
    assert manager.authorized('broadcaster')


def test_refresh_is_one_worker_transaction_and_survives_validation_restart(login):
    manager,api,at,client=login
    connect(login);at[0]+=50;manager.step()
    assert api.calls.count('refresh')==1
    assert not manager.authorized('broadcaster')
    manager.close();manager.recover();manager.step()
    assert manager.authorized('broadcaster') and api.calls.count('refresh')==1


def test_unknown_refresh_is_not_replayed_after_restart(login):
    manager,api,at,_=login
    connect(login);api.fail_refresh=True;at[0]+=50;manager.step()
    manager.close();manager.recover();manager.step();manager.step()
    assert api.calls.count('refresh')==1 and not manager.authorized('broadcaster')
    assert manager.snapshot()['accounts']['broadcaster']['state']=='authorization_required'


def test_failed_refresh_intent_write_never_sends_or_retries(login,monkeypatch):
    manager,api,at,_=login
    connect(login)
    monkeypatch.setattr(manager.store,'save',lambda *a:(_ for _ in ()).throw(PersistenceError('oauth_save_failed','login')))
    at[0]+=50;manager.step();manager.step()
    assert 'refresh' not in api.calls and not manager.authorized('broadcaster')


def test_manual_login_works_while_recording_stopped_but_auto_refresh_pauses(login):
    manager,api,at,_=login
    manager.recording=lambda:False
    connect(login);at[0]+=1000;manager.step()
    assert 'refresh' not in api.calls


def test_private_store_rejects_malformed_data_and_competing_owner(login):
    manager,api,at,_=login
    another=DeviceLogin(manager.accounts,manager.clients,manager.store,api,owned=lambda:True)
    with pytest.raises(PersistenceError,match='runtime_already_owned'):another.recover()
    assert manager.ready
    (manager.store.root/'broadcaster.json').write_text('[]')
    with pytest.raises(PersistenceError,match='invalid_oauth_file'):manager.store.load('broadcaster')


def test_oauth_rejects_non_twitch_login_urls():
    for uri in ('https://www.twitch.tv.attacker.test/activate','http://www.twitch.tv/activate','https://www.twitch.tv@attacker.test/activate'):
        api=TwitchOAuth(lambda *a,**k:HttpReply(200,dict(device_code='private',user_code='ABCDEFGH',verification_uri=uri,expires_in=600,interval=5)))
        with pytest.raises(TwitchFailure,match='oauth_invalid_response'):api.begin(OAuthAccount('fixture','123'),BASE_SCOPES)


def test_confidential_refresh_uses_body_and_public_omits_secret():
    calls=[]
    api=TwitchOAuth(lambda *a,**k:calls.append((a,k)) or HttpReply(400,{}))
    api.refresh(OAuthAccount('fixture','123','confidential','secret'),'refresh')
    api.refresh(OAuthAccount('fixture','123'),'refresh')
    assert calls[0][1]['data']['client_secret']=='secret'
    assert 'client_secret' not in calls[1][1]['data']
    assert all('secret' not in str(call[0]) for call in calls)


def test_login_web_is_inert_origin_checked_and_secrets_absent(login):
    from twitchbot.container import Container
    from twitchbot.web.app import create_app
    manager,api,_,_=login
    client=create_app(Container(login=manager)).test_client()
    assert client.get('/v2/connect').status_code==200
    assert client.get('/api/v2/login').headers['Cache-Control']=='no-store'
    assert client.post('/api/v2/login/start',json={'role':'broadcaster'}).status_code==403
    assert client.post('/api/v2/login/start',json={'role':'broadcaster'},headers={'Origin':'http://localhost'}).status_code==200
    assert api.calls==[]


def test_bootstrap_login_mode_never_sends_placeholder_credentials(tmp_path):
    from twitchbot.bootstrap import build_container
    private=tmp_path/'private';private.mkdir(mode=0o700)
    stage=tmp_path/'stage';stage.mkdir()
    forbidden=lambda *a,**k:pytest.fail('unexpected external request')
    container=build_container(tmp_path/'data.sqlite3',stage,None,'123',transport=forbidden,
        oauth_transport=forbidden,oauth_configuration={'private_root':str(private),'accounts':{'broadcaster':{'client_id':'fixture','user_id':'123'}}})
    container.runtime.start()
    try:
        container.operations.set_enabled(True,container.operations.settings.revision)
        container.operations.recorder.step()
        assert container.login.snapshot()['accounts']['broadcaster']['state']=='not_connected'
        assert not container.login.authorized('broadcaster')
    finally:container.runtime.stop()


def test_login_expiry_and_slow_down_do_not_poll_too_often(login):
    manager,api,at,_=login
    manager.begin('broadcaster');manager.step();at[0]+=5
    api.exchange=lambda *a:api.calls.append('exchange') or HttpReply(400,{'error':'slow_down'})
    manager.step();at[0]+=5;manager.step()
    assert api.calls.count('exchange')==1
    at[0]+=600;manager.step()
    assert manager.snapshot()['accounts']['broadcaster']['state']=='oauth_code_expired'


def test_login_validation_retries_new_pair_without_refresh_replay(login):
    manager,api,at,_=login
    connect(login);at[0]+=50;manager.step()
    original=api.validate
    api.validate=lambda *a:HttpReply(503,{})
    manager.step()
    assert manager.records['broadcaster']['candidate'] is not None
    assert not manager.authorized('broadcaster')
    at[0]+=30;api.validate=original;manager.step()
    assert manager.authorized('broadcaster') and api.calls.count('refresh')==1


def test_scope_denial_does_not_replace_existing_account(login):
    manager,api,at,client=login
    connect(login);old=client.credentials
    manager.begin('broadcaster',predictions=True);manager.step();at[0]+=5;manager.step()
    api.scopes=[];manager.step()
    assert manager.snapshot()['accounts']['broadcaster']['state']=='oauth_scope_required'
    assert client.credentials==old


def test_snapshot_does_not_wait_for_oauth_network(login):
    from threading import Event, Thread
    manager,api,_,_=login
    entered,release=Event(),Event();original=api.begin
    def blocked(*args):
        entered.set();release.wait(3);return original(*args)
    api.begin=blocked;manager.begin('broadcaster')
    worker=Thread(target=manager.step);worker.start()
    try:
        assert entered.wait(1)
        assert manager.snapshot()['accounts']['broadcaster']['state']=='starting'
        manager.cancel('broadcaster')
    finally:release.set();worker.join(3)
    assert not worker.is_alive()
    assert manager.snapshot()['accounts']['broadcaster']['state']=='cancelled'
