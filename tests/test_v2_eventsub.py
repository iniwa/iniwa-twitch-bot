from collections import deque
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from twitchbot.adapters.eventsub import EventSubSession, _trusted_url
from twitchbot.adapters.twitch import HelixClient, HttpReply, TwitchFailure
from twitchbot.adapters.persistence import SQLiteDatabase
from twitchbot.adapters.persistence.community import CommunityRepository
from twitchbot.application.event_recording import EventRecorder

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


@pytest.mark.parametrize('scope', ['channel:read:predictions', 'channel:manage:predictions', None])
def test_prediction_end_subscription_accepts_either_authorized_scope(scope):
    from twitchbot.adapters.eventsub import subscription_specs
    client = SimpleNamespace(channel_id='123', credentials=SimpleNamespace(user_id='123'),
                             scopes=frozenset([scope] if scope else []))
    predictions = [spec for spec in subscription_specs(client) if spec[0] == 'channel.prediction.end']
    assert predictions == ([('channel.prediction.end', '1', {'broadcaster_user_id':'123'}, scope)] if scope else [])


def notification(kind, event, *, key='event', at=NOW):
    return {'metadata': {'message_type': 'notification', 'subscription_type': kind,
                         'message_timestamp': at.isoformat(), 'message_id': key},
            'payload': {'subscription': {'type': kind}, 'event': {'broadcaster_user_id': '123', **event}}}


def test_eventsub_online_captures_early_chat_without_fabricating_viewer_count(tmp_path):
    db = SQLiteDatabase(tmp_path/'events.sqlite3'); db.migrate()
    repository = CommunityRepository(db, '123', clock=lambda: NOW+timedelta(seconds=5))
    published = []
    recorder = EventRecorder(repository, publish_transition=published.append)
    online = notification('stream.online', {'id': 's1', 'started_at': NOW.isoformat()})
    assert recorder.ingest(online)['state'] == 'stream_transition'
    assert published[0].viewer_count is None
    message = notification('channel.chat.message', {'chatter_user_id': 'u1', 'message_id': 'chat1',
        'message': {'text': 'hello'}}, key='envelope-chat', at=NOW+timedelta(seconds=1))
    assert recorder.ingest(message)['state'] == 'recorded'
    assert recorder.ingest(message)['state'] == 'not_recorded'
    assert repository.chats()['total'] == 1
    offline = notification('stream.offline', {}, key='offline', at=NOW+timedelta(seconds=2))
    recorder.ingest(offline)
    follow = notification('channel.follow', {'user_id': 'u2', 'followed_at': (NOW+timedelta(seconds=3)).isoformat()}, key='follow', at=NOW+timedelta(seconds=3))
    assert recorder.ingest(follow)['state'] == 'recorded'
    assert recorder.ingest(follow)['state'] == 'duplicate'
    assert repository.events()['items'][0]['attribution'] == 'offline'


def test_unknown_interval_and_other_channels_are_not_assigned_to_current_stream(tmp_path):
    db = SQLiteDatabase(tmp_path/'events.sqlite3'); db.migrate()
    repository = CommunityRepository(db, '123', clock=lambda: NOW+timedelta(seconds=100))
    recorder = EventRecorder(repository)
    recorder.ingest(notification('stream.online', {'id': 's1', 'started_at': NOW.isoformat()}))
    recorder.ingest(notification('channel.follow', {'user_id': 'u', 'followed_at': (NOW+timedelta(seconds=60)).isoformat()}, key='late', at=NOW+timedelta(seconds=60)))
    assert repository.events()['items'][0]['attribution'] == 'unknown'
    from twitchbot.application.persistence import PersistenceError
    with pytest.raises(PersistenceError):
        recorder.ingest(notification('channel.follow', {'broadcaster_user_id': '999', 'user_id': 'u', 'followed_at': NOW.isoformat()}))


class Socket:
    def __init__(self, messages):
        self.messages, self.closed = deque(messages), False
    def recv(self):
        if not self.messages:
            raise TimeoutError
        return json.dumps(self.messages.popleft())
    def close(self): self.closed = True


def welcome(key):
    return {'metadata': {'message_type': 'session_welcome'}, 'payload': {'session': {'id': key, 'keepalive_timeout_seconds': 30}}}


@pytest.fixture
def session():
    class Client:
        channel_id = '123'
        credentials = SimpleNamespace(user_id='123')
        scopes = frozenset({'moderator:read:followers'})
        calls = []
        _data = staticmethod(HelixClient._data)
        def status(self): return {'read': True}
        def request(self, method, endpoint, **kwargs):
            self.calls.append(kwargs['body'])
            body = kwargs['body']
            return HttpReply(202, {'data': [{**body, 'status': 'enabled'}]})
    class Recording:
        records, gaps = [], []
        def ingest(self, message): self.records.append(message)
        def gap(self, reason): self.gaps.append(reason)
        def connected(self): pass
    clock, connections = [100.0], []
    sockets = deque([Socket([welcome('first')]), Socket([welcome('second')])])
    def connect(url):
        connections.append(url)
        return sockets.popleft()
    value = EventSubSession(Client(), Recording(), running=lambda: True, connect=connect, monotonic=lambda: clock[0])
    return value, clock, connections


def test_websocket_welcome_subscribe_keepalive_and_stop(session):
    value, clock, connections = session
    assert connections == []
    assert value.step()['state'] == 'connected'
    assert len(value.client.calls) == 4
    assert value.step()['state'] == 'connected'
    socket = value.socket
    value.running = lambda: False
    assert value.step()['state'] == 'paused' and socket.closed
    assert value.recorder.gaps == ['stopped']


def test_reconnect_waits_for_new_welcome_and_does_not_resubscribe(session):
    value, clock, connections = session
    value.step(); old = value.socket
    old.messages.append({'metadata': {'message_type': 'session_reconnect'},
        'payload': {'session': {'reconnect_url': 'wss://eventsub.wss.twitch.tv/ws?reconnect=fixture'}}})
    assert value.step()['state'] == 'connected'
    assert old.closed and value.session_id == 'second'
    assert len(value.client.calls) == 4


def test_expired_keepalive_records_gap_and_reconnects_with_backoff(session):
    value, clock, connections = session
    value.step(); clock[0] += 31
    assert value.step()['state'] == 'reconnecting'
    assert value.recorder.gaps == ['disconnected']
    value.step(); assert len(connections) == 1
    clock[0] += 5
    assert value.step()['state'] == 'connected'
    assert len(value.client.calls) == 8


@pytest.mark.parametrize('url', ['ws://eventsub.wss.twitch.tv', 'wss://example.com',
    'wss://eventsub.wss.twitch.tv.evil/ws', 'wss://u@eventsub.wss.twitch.tv', 'wss://eventsub.wss.twitch.tv:8443/ws'])
def test_reconnect_never_follows_untrusted_destinations(url):
    with pytest.raises(TwitchFailure): _trusted_url(url)
