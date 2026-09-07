"""Explicit disposable fixture with real workers/NAS and synthetic Twitch only."""

import atexit
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from threading import current_thread
from queue import Queue, Empty

from flask import request

from twitchbot.adapters.twitch import HttpReply, TwitchCredentials
from twitchbot.adapters.persistence.backups import BackupLimits
from twitchbot.bootstrap import build_container
from twitchbot.web.app import create_app as create_v2_app
from twitchbot.application.login import BASE_SCOPES


def create_app():
    temporary = TemporaryDirectory(prefix='operational-v2-')
    root = Path(temporary.name)
    staging = root/'snapshots'; staging.mkdir()
    initial = datetime.now(timezone.utc)-timedelta(minutes=20)
    calls, stream_state = [], {'live': True}
    messages, predictions, sent = Queue(), [], []
    login_mode = os.environ.get('QA_LOGIN') == '1'
    authorization = {'approved':set(), 'refreshes':0, 'offset':0}
    owner_client = 'ownerfixture' if login_mode else 'fixture'

    def transport(method, url, **kwargs):
        calls.append({'method': method, 'thread': current_thread().name})
        if url.endswith('/validate'):
            return HttpReply(200, {'client_id': owner_client, 'user_id': '123',
                'scopes': [*BASE_SCOPES, 'channel:manage:predictions'], 'expires_in': 7200})
        if url.endswith('/channels/followers'):
            return HttpReply(200, {'total': 0, 'data': [], 'pagination': {}})
        if url.endswith('/streams'):
            return HttpReply(200, {'data': [{'user_id': '123', 'id': 's1', 'title': '検証用 — 架空配信',
                'game_name': 'サンプル', 'viewer_count': 12, 'started_at': initial.isoformat()}] if stream_state['live'] else []})
        if url.endswith('/channels'):
            return HttpReply(200, {'data': [{'broadcaster_id': '123', 'title': '検証用 — 架空配信',
                'game_id': '1', 'game_name': 'サンプル', 'tags': ['日本語']}]})
        if url.endswith('/eventsub/subscriptions'):
            return HttpReply(202, {'data': [{**kwargs['json'], 'status': 'enabled'}]})
        if url.endswith('/predictions'):
            if method == 'POST':
                body = kwargs['json']
                predictions[:] = [dict(id='qa-prediction', broadcaster_id='123', title=body['title'], status='ACTIVE', outcomes=[dict(id=str(i), title=o['title']) for i,o in enumerate(body['outcomes'])])]
            if method == 'PATCH':
                predictions[0].update(status=kwargs['json']['status'], winning_outcome_id=kwargs['json'].get('winning_outcome_id'))
            return HttpReply(200, {'data': predictions})
        raise AssertionError('unexpected fixture request')

    def bot_transport(method, url, **kwargs):
        calls.append({'method': method, 'thread': current_thread().name})
        if url.endswith('/validate'):
            return HttpReply(200, {'client_id': 'botfixture' if login_mode else 'fixture', 'user_id': '999', 'scopes': ['user:write:chat'], 'expires_in': 7200})
        if url.endswith('/shared_chat/session'):
            return HttpReply(200, {'data': []})
        if url.endswith('/chat/messages'):
            sent.append(kwargs['json']['message'])
            return HttpReply(200, {'data': [{'is_sent': True, 'message_id': 'qa-sent-'+str(len(sent))}]})
        raise AssertionError('unexpected fixture bot request')

    class Socket:
        first = True
        def recv(self):
            if self.first:
                self.first = False
                return json.dumps({'metadata': {'message_type': 'session_welcome'},
                    'payload': {'session': {'id': 'fixture-session', 'keepalive_timeout_seconds': 30}}})
            try:
                return messages.get_nowait()
            except Empty:
                return json.dumps({'metadata': {'message_type': 'session_keepalive'}, 'payload': {}})
        def close(self): pass

    nas_root = os.environ.get('QA_NAS_ROOT')
    destination = mkdtemp(prefix='operational-v2-', dir=nas_root) if nas_root else None

    def oauth_transport(method, url, **kwargs):
        calls.append({'method':method, 'thread':current_thread().name})
        body = kwargs.get('data', {})
        role = 'bot' if body.get('client_id') == 'botfixture' or 'bot-access' in kwargs.get('headers', {}).get('Authorization', '') else 'broadcaster'
        if url.endswith('/device'):
            return HttpReply(200, {'device_code':role+'-private-device', 'user_code':'BOTCODE1' if role=='bot' else 'OWNERCOD', 'verification_uri':'https://www.twitch.tv/activate', 'expires_in':600, 'interval':5})
        if url.endswith('/token'):
            if body['grant_type']=='refresh_token': authorization['refreshes']+=1
            elif role not in authorization['approved']: return HttpReply(400, {'message':'authorization_pending'})
            return HttpReply(200, {'access_token':('bot-access' if role=='bot' else 'owner-access')+str(authorization['refreshes']), 'refresh_token':'private-refresh-'+role, 'token_type':'bearer', 'expires_in':600})
        if url.endswith('/validate'):
            return HttpReply(200, {'client_id':'botfixture' if role=='bot' else 'ownerfixture', 'user_id':'999' if role=='bot' else '123', 'scopes':['user:write:chat'] if role=='bot' else list(BASE_SCOPES)+['channel:manage:predictions'], 'expires_in':600})
        raise AssertionError('unexpected synthetic OAuth request')

    oauth_config = None
    if login_mode:
        private = root/'private'; private.mkdir(mode=0o700)
        oauth_config = {'private_root':str(private), 'accounts':{'broadcaster':{'client_id':'ownerfixture','user_id':'123'},'bot':{'client_id':'botfixture','user_id':'999'}}}
    container = build_container(root/'fixture.sqlite3', staging,
        None if login_mode else TwitchCredentials('fixture', '123', 'synthetic-token'), '123', transport=transport,
        nas_root=destination, nas_source=os.environ.get('QA_NAS_SOURCE') if destination else None,
        backup_limits=BackupLimits(reserve_bytes=0, reserve_fraction=0), eventsub_connect=lambda url: Socket(),
        bot_credentials=None if login_mode else TwitchCredentials('fixture', '999', 'synthetic-bot-token'), bot_transport=bot_transport,
        oauth_configuration=oauth_config, oauth_transport=oauth_transport if login_mode else None)
    if login_mode:
        import time
        container.login.clock = lambda:time.time()+authorization['offset']
    app = create_v2_app(container)
    @app.after_request
    def label(response):
        response.headers['X-Device-QA'] = 'synthetic'
        return response
    app.extensions['device_qa.temporary'] = temporary
    container.runtime.start()
    atexit.register(container.runtime.stop)

    @app.get('/qa/state')
    def state():
        return {'synthetic': True, 'calls': len(calls),
                'request_thread_calls': [c for c in calls if c['thread'] not in ('recording', 'followers', 'events', 'chat', 'predictions', 'login')],
                'sent': list(sent),
                'refreshes':authorization['refreshes'],
                'nas_fixture': Path(destination).name if destination else None}

    @app.post('/qa/stream')
    def change_stream():
        body = request.get_json()
        assert body.get('live') is False
        stream_state['live'] = False
        next(w for w in container.runtime.workers if w.name == 'recording').wake()
        return {'ok': True}

    @app.post('/qa/login/approve')
    def approve_login():
        assert login_mode
        role=request.get_json()['role'];assert role in ('broadcaster','bot')
        authorization['approved'].add(role)
        container.login.wake()
        return {'ok':True}

    @app.post('/qa/login/expire')
    def expire_login():
        assert login_mode
        authorization['offset']+=601
        container.login.wake()
        return {'ok':True}

    @app.post('/qa/chat')
    def chat():
        body = request.get_json()
        from uuid import uuid4
        key = uuid4().hex
        messages.put(json.dumps({'metadata': {'message_type': 'notification', 'subscription_type': 'channel.chat.message', 'message_id': key, 'message_timestamp': datetime.now(timezone.utc).isoformat()},
            'payload': {'subscription': {'type': 'channel.chat.message'}, 'event': {'broadcaster_user_id': '123', 'chatter_user_id': '456', 'chatter_user_name': '架空の視聴者', 'chatter_user_login': 'synthetic_viewer', 'message_id': key, 'message': {'text': body['text']}, 'badges': []}}}))
        return {'queued': True}

    return app
