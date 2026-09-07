"""Single EventSub WebSocket session with explicit lifecycle and bounded reads."""

import json
import time
from urllib.parse import urlsplit

from .twitch import TwitchFailure
from ..application.persistence import PersistenceError

DEFAULT_URL = 'wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30'


def _trusted_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'wss' or parsed.hostname != 'eventsub.wss.twitch.tv' or parsed.port not in (None, 443) or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError
    except (TypeError, ValueError):
        raise TwitchFailure('invalid_reconnect_url') from None
    return url


class WebSocketConnection:
    def __init__(self, url):
        import websocket
        self.library = websocket
        # TLS verification stays enabled; EventSub gets no OAuth headers.
        try:
            self.socket = websocket.create_connection(_trusted_url(url), timeout=3,
                enable_multithread=True, http_no_proxy=['eventsub.wss.twitch.tv'])
            self.socket.settimeout(.5)
        except (websocket.WebSocketException, OSError):
            raise TwitchFailure('eventsub_connection_failed') from None

    def recv(self):
        try:
            return self.socket.recv()
        except self.library.WebSocketTimeoutException:
            raise TimeoutError from None
        except (self.library.WebSocketException, OSError):
            raise TwitchFailure('eventsub_connection_failed') from None

    def close(self):
        try:
            self.socket.close(timeout=1)
        except (self.library.WebSocketException, OSError):
            pass


def subscription_specs(client):
    channel, user = client.channel_id, client.credentials.user_id
    specs = [('stream.online', '1', {'broadcaster_user_id': channel}, None),
             ('stream.offline', '1', {'broadcaster_user_id': channel}, None),
             ('channel.raid', '1', {'to_broadcaster_user_id': channel}, None)]
    if 'user:read:chat' in client.scopes:
        specs.append(('channel.chat.message', '1', {'broadcaster_user_id': channel, 'user_id': user}, 'user:read:chat'))
    if user == channel:
        scopes = [('channel.follow', '2', 'moderator:read:followers'),
                  ('channel.subscribe', '1', 'channel:read:subscriptions'),
                  ('channel.subscription.message', '1', 'channel:read:subscriptions'),
                  ('channel.subscription.gift', '1', 'channel:read:subscriptions'),
                  ('channel.cheer', '1', 'bits:read'),
                  ('channel.channel_points_custom_reward_redemption.add', '1', 'channel:read:redemptions'),
                  ('channel.prediction.end', '1', 'channel:manage:predictions' if 'channel:manage:predictions' in client.scopes else 'channel:read:predictions')]
        for kind, version, scope in scopes:
            if scope in client.scopes:
                condition = {'broadcaster_user_id': channel}
                if kind == 'channel.follow':
                    condition['moderator_user_id'] = user
                specs.append((kind, version, condition, scope))
    return specs


class EventSubSession:
    def __init__(self, client, recorder, *, running=lambda: False, connect=WebSocketConnection,
                 monotonic=time.monotonic):
        self.client, self.recorder, self.running = client, recorder, running
        self.connect, self.clock = connect, monotonic
        self.socket, self.session_id = None, None
        self.deadline, self.retry_at = 0, 0
        self.keepalive = 30
        self.subscriptions, self.revoked = set(), set()
        self.state = 'not_started'
        self.credential_revision = getattr(client, 'credential_revision', 0)

    @staticmethod
    def _message(raw):
        if not isinstance(raw, str) or not raw or len(raw.encode('utf-8')) > 2*1024**2:
            raise TwitchFailure('invalid_eventsub_frame')
        try:
            message = json.loads(raw)
            if not isinstance(message, dict) or not isinstance(message['metadata'], dict) or not isinstance(message['payload'], dict):
                raise ValueError
            return message
        except (KeyError, ValueError, TypeError):
            raise TwitchFailure('invalid_eventsub_frame') from None

    def _welcome(self, message):
        try:
            if message['metadata']['message_type'] != 'session_welcome':
                raise ValueError
            session = message['payload']['session']
            key, keepalive = session['id'], session['keepalive_timeout_seconds']
            if not isinstance(key, str) or not key or len(key) > 1000:
                raise ValueError
            if keepalive is not None and (type(keepalive) is not int or not 10 <= keepalive <= 600):
                raise ValueError
            return key, keepalive or self.keepalive
        except (KeyError, ValueError, TypeError):
            raise TwitchFailure('invalid_eventsub_welcome') from None

    def _subscribe(self):
        self.subscriptions.clear()
        for kind, version, condition, scope in subscription_specs(self.client):
            if kind in self.revoked:
                continue
            reply = self.client.request('POST', 'eventsub/subscriptions', scope=scope,
                body={'type': kind, 'version': version, 'condition': condition,
                      'transport': {'method': 'websocket', 'session_id': self.session_id}})
            rows = self.client._data(reply)
            if len(rows) != 1 or rows[0].get('status') != 'enabled' or rows[0].get('type') != kind or rows[0].get('transport', {}).get('session_id') != self.session_id:
                raise TwitchFailure('eventsub_subscription_unconfirmed')
            self.subscriptions.add(kind)
        self.recorder.connected()

    def _notification(self, message):
        kind = message['payload']['subscription']['type']
        if kind not in self.subscriptions:
            raise TwitchFailure('unexpected_subscription')
        return self.recorder.ingest(message)

    def _handoff(self, url):
        replacement = self.connect(_trusted_url(url))
        try:
            session_id, keepalive = self._welcome(self._message(replacement.recv()))
            # Drain bounded in-flight notifications before closing the old socket.
            for _ in range(100):
                try:
                    message = self._message(self.socket.recv())
                except TimeoutError:
                    break
                if message['metadata']['message_type'] == 'notification':
                    self._notification(message)
            else:
                self.recorder.gap('disconnected')
            old, self.socket = self.socket, replacement
            replacement = None
            self.session_id, self.keepalive = session_id, keepalive
            self.deadline = self.clock()+self.keepalive
            old.close()
        finally:
            if replacement is not None:
                replacement.close()

    def close(self, reason='stopped'):
        active = self.socket is not None or self.state not in ('not_started', 'stopped')
        try:
            if self.socket is not None:
                self.socket.close()
        finally:
            self.socket, self.session_id, self.state = None, None, 'stopped'
            self.subscriptions.clear()
            if active:
                self.recorder.gap(reason)

    def step(self):
        revision = getattr(self.client, 'credential_revision', 0)
        if revision != self.credential_revision:
            self.close('authorization')
            self.revoked.clear()
            self.retry_at, self.credential_revision = 0, revision
        if not self.running():
            self.close()
            return {'state': 'paused'}
        if not self.client.status()['read']:
            self.close('authorization')
            return {'state': 'authorization_required'}
        if self.clock() < self.retry_at:
            return {'state': 'reconnecting'}
        try:
            if self.socket is None:
                self.socket = self.connect(DEFAULT_URL)
                self.state = 'welcoming'
                self.deadline = self.clock()+10
            try:
                message = self._message(self.socket.recv())
            except TimeoutError:
                if self.clock() > self.deadline:
                    raise TwitchFailure('eventsub_keepalive_expired')
                return {'state': self.state, 'subscriptions': sorted(self.subscriptions)}
            kind = message['metadata']['message_type']
            if self.state == 'welcoming':
                self.session_id, self.keepalive = self._welcome(message)
                self._subscribe()
                self.state, self.deadline = 'connected', self.clock()+self.keepalive
            elif kind in ('session_keepalive', 'notification'):
                self.deadline = self.clock()+self.keepalive
                if kind == 'notification':
                    self._notification(message)
            elif kind == 'session_reconnect':
                self._handoff(message['payload']['session']['reconnect_url'])
            elif kind == 'revocation':
                subscription = message['payload']['subscription']
                self.revoked.add(subscription['type'])
                self.subscriptions.discard(subscription['type'])
                if subscription.get('status') == 'authorization_revoked':
                    self.client.invalidate()
                self.recorder.gap('authorization')
                return {'state': 'action_required', 'subscriptions': sorted(self.subscriptions)}
            else:
                raise TwitchFailure('unexpected_eventsub_message')
            return {'state': self.state, 'subscriptions': sorted(self.subscriptions)}
        except (TwitchFailure, PersistenceError, KeyError, TypeError, ValueError, OSError):
            self.close('disconnected')
            self.retry_at = self.clock()+5
            return {'state': 'reconnecting'}
