"""Bounded Helix transport, explicit token validation and non-retrying writes."""

from collections import deque
from dataclasses import dataclass, field
import json
from threading import RLock
import time

from ..application.control import ActionResult, ChannelUpdate


class TwitchFailure(Exception):
    def __init__(self, code, *, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TwitchCredentials:
    client_id: str = field(repr=False)
    user_id: str = field(repr=False)
    access_token: str = field(repr=False)

    def __post_init__(self):
        for value in (self.client_id, self.user_id, self.access_token):
            if not isinstance(value, str) or not value or any(c.isspace() for c in value):
                raise TwitchFailure('invalid_credentials')
        if not self.user_id.isascii() or not self.user_id.isdigit():
            raise TwitchFailure('invalid_credentials')
        object.__setattr__(self, 'access_token', self.access_token.removeprefix('oauth:'))
        if not self.access_token:
            raise TwitchFailure('invalid_credentials')


@dataclass(frozen=True, slots=True)
class HttpReply:
    status: int
    data: object = field(default=None, repr=False)
    headers: dict = field(default_factory=dict, repr=False)


class RequestsTransport:
    def __call__(self, method, url, **kwargs):
        import requests
        try:
            # A fresh session avoids cross-thread cookie/header state and redirects.
            with requests.Session() as session:
                session.trust_env = False
                with session.request(method, url, timeout=(3, 7), allow_redirects=False,
                                     stream=True, **kwargs) as response:
                    content = bytearray()
                    for chunk in response.iter_content(16384):
                        content.extend(chunk)
                        if len(content) > 2*1024**2:
                            raise TwitchFailure('response_too_large', uncertain=method != 'GET')
                    try:
                        data = json.loads(content) if content else None
                    except (ValueError, UnicodeDecodeError):
                        data = None
                    return HttpReply(response.status_code, data, dict(response.headers))
        except requests.RequestException:
            raise TwitchFailure('transport_failed', uncertain=method != 'GET') from None


class HelixClient:
    """Construct/status are inert. The worker calls validate at start and hourly.

    One instance represents one validated user identity. Tokens never enter SQLite,
    logs or status output. All writes are single attempts, including 5xx/timeouts.
    """
    def __init__(self, credentials, channel_id, *, transport=None, monotonic=time.monotonic,
                 wall_time=time.time, allowed=lambda: True):
        if not isinstance(credentials, TwitchCredentials) or not isinstance(channel_id, str) or not channel_id.isascii() or not channel_id.isdigit():
            raise TwitchFailure('invalid_credentials')
        self.credentials, self.channel_id = credentials, channel_id
        self.transport = transport or RequestsTransport()
        self.clock, self.wall_time, self.allowed = monotonic, wall_time, allowed
        self.lock = RLock()
        self._valid_until, self._blocked_until = 0, 0
        self._scopes, self._recent = frozenset(), deque()
        self._state = 'not_validated'
        self.credential_revision = 0

    def replace_credentials(self, credentials):
        """Install a validated grant for the same account; never replay a request."""
        if not isinstance(credentials, TwitchCredentials) or (credentials.client_id, credentials.user_id) != (self.credentials.client_id, self.credentials.user_id):
            raise TwitchFailure('authorization_identity_mismatch')
        with self.lock:
            self.credentials = credentials
            self._valid_until, self._scopes, self._state = 0, frozenset(), 'not_validated'
            self.credential_revision += 1

    def status(self):
        with self.lock:
            valid = self._state == 'ready' and self.clock() < self._valid_until
            owner = self.credentials.user_id == self.channel_id
            return {'state': 'ready' if valid else ('validation_due' if self._state == 'ready' else self._state),
                    'read': valid, 'markers': valid and owner and 'channel:manage:broadcast' in self._scopes,
                    'presets': valid and owner and 'channel:manage:broadcast' in self._scopes,
                    'followers': valid and owner and 'moderator:read:followers' in self._scopes,
                    'chat_read': valid and 'user:read:chat' in self._scopes}

    @property
    def available(self):
        return self.status()['presets']

    @property
    def scopes(self):
        with self.lock:
            return self._scopes if self.status()['read'] else frozenset()

    def invalidate(self):
        with self.lock:
            self._valid_until, self._scopes, self._state = 0, frozenset(), 'authorization_required'

    def _call(self, method, url, **kwargs):
        now = self.clock()
        if not self.allowed():
            raise TwitchFailure('runtime_stopped')
        while self._recent and self._recent[0] <= now-30:
            self._recent.popleft()
        if now < self._blocked_until or len(self._recent) >= 30:
            raise TwitchFailure('rate_limited')
        self._recent.append(now)
        try:
            reply = self.transport(method, url, **kwargs)
        except TwitchFailure:
            raise
        except Exception:
            raise TwitchFailure('transport_failed', uncertain=method != 'GET') from None
        if not isinstance(reply, HttpReply):
            raise TwitchFailure('invalid_response', uncertain=method != 'GET')
        headers = {str(k).lower(): v for k, v in reply.headers.items()}
        if reply.status == 429 or headers.get('ratelimit-remaining') == '0':
            try:
                delay = max(1, min(3600, float(headers.get('ratelimit-reset'))-self.wall_time()))
            except (ValueError, TypeError):
                delay = 60
            self._blocked_until = now+delay
        if reply.status == 401:
            self._valid_until, self._scopes, self._state = 0, frozenset(), 'authorization_required'
        return reply

    def validate(self, *, force=False):
        with self.lock:
            if not force and self.status()['read']:
                return self.status()
            self._valid_until, self._scopes = 0, frozenset()
            try:
                reply = self._call('GET', 'https://id.twitch.tv/oauth2/validate',
                    headers={'Authorization': 'OAuth '+self.credentials.access_token})
                data = reply.data
                if reply.status != 200:
                    raise TwitchFailure('authorization_required' if reply.status in (401, 403) else 'validation_unavailable')
                if not isinstance(data, dict) or data.get('client_id') != self.credentials.client_id or data.get('user_id') != self.credentials.user_id:
                    raise TwitchFailure('authorization_identity_mismatch')
                scopes, expires = data.get('scopes'), data.get('expires_in')
                if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes) or type(expires) is not int or expires <= 60:
                    raise TwitchFailure('authorization_required')
                self._valid_until = self.clock()+min(3600, expires-60)
                self._scopes, self._state = frozenset(scopes), 'ready'
            except TwitchFailure as exc:
                self._state = exc.code
                raise
            return self.status()

    def request(self, method, endpoint, *, params=None, body=None, scope=None, owner=False):
        endpoints = {'streams', 'channels', 'channels/followers', 'search/categories',
                     'streams/markers', 'eventsub/subscriptions', 'chat/messages', 'shared_chat/session', 'predictions', 'videos', 'games'}
        if endpoint not in endpoints or method not in ('GET', 'POST', 'PATCH', 'DELETE'):
            raise TwitchFailure('invalid_endpoint')
        with self.lock:
            if not self.status()['read']:
                raise TwitchFailure('authorization_validation_required')
            if (owner and self.credentials.user_id != self.channel_id) or (scope and scope not in self._scopes):
                raise TwitchFailure('authorization_scope_required')
            reply = self._call(method, 'https://api.twitch.tv/helix/'+endpoint,
                headers={'Client-ID': self.credentials.client_id, 'Authorization': 'Bearer '+self.credentials.access_token},
                params=params, json=body)
            if not 200 <= reply.status < 300:
                code = {401: 'authorization_required', 403: 'authorization_scope_required',
                        429: 'rate_limited'}.get(reply.status, 'twitch_rejected')
                raise TwitchFailure(code, uncertain=method != 'GET' and reply.status >= 500)
            return reply

    @staticmethod
    def _data(reply):
        if not isinstance(reply.data, dict) or not isinstance(reply.data.get('data'), list):
            raise TwitchFailure('invalid_response')
        return reply.data['data']

    def stream(self):
        rows = self._data(self.request('GET', 'streams', params={'user_id': self.channel_id}))
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('user_id') != self.channel_id:
            raise TwitchFailure('invalid_response')
        return rows[0]

    def channel(self):
        rows = self._data(self.request('GET', 'channels', params={'broadcaster_id': self.channel_id}))
        if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('broadcaster_id') != self.channel_id:
            raise TwitchFailure('invalid_response')
        return rows[0]

    def followers_page(self, cursor=None):
        params = {'broadcaster_id': self.channel_id, 'first': 100}
        if cursor:
            params['after'] = cursor
        reply = self.request('GET', 'channels/followers', params=params,
                             scope='moderator:read:followers', owner=True)
        self._data(reply)
        return reply.data

    def shared_chat_active(self):
        return bool(self._data(self.request('GET', 'shared_chat/session', params={'broadcaster_id': self.channel_id})))

    def _prediction(self, row):
        try:
            if not isinstance(row, dict) or row['broadcaster_id'] != self.channel_id or row['status'] not in ('ACTIVE', 'LOCKED', 'RESOLVED', 'CANCELED'):
                raise ValueError
            if not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 200 or not isinstance(row['title'], str) or not 1 <= len(row['title']) <= 45 or not isinstance(row['outcomes'], list) or not 2 <= len(row['outcomes']) <= 10:
                raise ValueError
            outcomes = []
            for outcome in row['outcomes']:
                if not isinstance(outcome['id'], str) or not 1 <= len(outcome['id']) <= 200 or not isinstance(outcome['title'], str) or not 1 <= len(outcome['title']) <= 25:
                    raise ValueError
                outcomes.append({'id': outcome['id'], 'title': outcome['title']})
            if len({o['id'] for o in outcomes}) != len(outcomes):
                raise ValueError
            winner = row.get('winning_outcome_id')
            if winner is not None and winner not in {o['id'] for o in outcomes}:
                raise ValueError
            return {'id': row['id'], 'title': row['title'], 'status': row['status'], 'outcomes': outcomes, 'winning_outcome_id': winner}
        except (KeyError, TypeError, ValueError):
            raise TwitchFailure('invalid_prediction_response') from None

    def predictions(self):
        scope = 'channel:manage:predictions' if 'channel:manage:predictions' in self.scopes else 'channel:read:predictions'
        rows = self._data(self.request('GET', 'predictions', params={'broadcaster_id': self.channel_id, 'first': 20}, scope=scope, owner=True))
        return [self._prediction(row) for row in rows]

    def change_prediction(self, action, payload):
        if action == 'start':
            from ..application.predictions import prediction_spec
            spec = prediction_spec({k: payload[k] for k in ('title', 'outcomes', 'prediction_window')})
            body = dict(broadcaster_id=self.channel_id, title=spec['title'], outcomes=[{'title': title} for title in spec['outcomes']], prediction_window=spec['prediction_window'])
        elif action in ('lock', 'resolve', 'cancel'):
            body = dict(broadcaster_id=self.channel_id, id=payload['id'], status={'lock': 'LOCKED', 'resolve': 'RESOLVED', 'cancel': 'CANCELED'}[action])
            if action == 'resolve':
                body['winning_outcome_id'] = payload['winning_outcome_id']
        else:
            raise TwitchFailure('invalid_prediction_action')
        reply = self.request('POST' if action == 'start' else 'PATCH', 'predictions', body=body, scope='channel:manage:predictions', owner=True)
        try:
            rows = self._data(reply)
            if len(rows) != 1:
                raise TwitchFailure('invalid_prediction_response')
            result = self._prediction(rows[0])
            if action == 'start':
                if result['title'] != body['title'] or [o['title'] for o in result['outcomes']] != spec['outcomes'] or result['status'] != 'ACTIVE':
                    raise TwitchFailure('invalid_prediction_response')
            elif result['id'] != body['id'] or result['status'] != body['status'] or (action == 'resolve' and result['winning_outcome_id'] != body['winning_outcome_id']):
                raise TwitchFailure('invalid_prediction_response')
            return result
        except TwitchFailure:
            raise TwitchFailure('invalid_prediction_response', uncertain=True) from None

    def send_chat(self, message):
        if not isinstance(message, str) or not message.strip() or len(message) > 500 or any(ord(c) < 32 for c in message):
            raise TwitchFailure('invalid_chat_message')
        if self.shared_chat_active():
            raise TwitchFailure('shared_chat_paused')
        reply = self.request('POST', 'chat/messages', scope='user:write:chat', body={
            'broadcaster_id': self.channel_id, 'sender_id': self.credentials.user_id, 'message': message})
        try:
            rows = self._data(reply)
            if len(rows) != 1 or type(rows[0].get('is_sent')) is not bool:
                raise ValueError
            if rows[0]['is_sent'] and isinstance(rows[0].get('message_id'), str) and rows[0]['message_id'] and not rows[0].get('drop_reason'):
                return 'sent'
            if not rows[0]['is_sent']:
                return 'failed'
        except (TwitchFailure, TypeError, ValueError, AttributeError):
            pass
        raise TwitchFailure('invalid_chat_result', uncertain=True)

    def create_marker(self, channel_id, description):
        if channel_id != self.channel_id or not isinstance(description, str) or len(description) > 140:
            return ActionResult('failed')
        try:
            reply = self.request('POST', 'streams/markers', body={'user_id': channel_id, 'description': description},
                                 scope='channel:manage:broadcast', owner=True)
            rows = self._data(reply)
            if len(rows) != 1:
                return ActionResult('unknown')
            return ActionResult('succeeded', rows[0]['id'], rows[0]['position_seconds'])
        except TwitchFailure as exc:
            return ActionResult('unknown' if exc.uncertain or exc.code == 'invalid_response' else 'failed')
        except (KeyError, TypeError, ValueError):
            return ActionResult('unknown')

    def apply_preset(self, channel_id, preset):
        if channel_id != self.channel_id or not isinstance(preset, ChannelUpdate) or not isinstance(preset.title, str) or not 1 <= len(preset.title) <= 140 or len(preset.tags) > 10 or any(not isinstance(t, str) or not 1 <= len(t) <= 25 or not t.isalnum() for t in preset.tags):
            return ActionResult('failed')
        body = {'title': preset.title, 'tags': list(preset.tags)}
        if preset.game_id is not None:
            body['game_id'] = preset.game_id
        try:
            self.request('PATCH', 'channels', params={'broadcaster_id': channel_id}, body=body,
                         scope='channel:manage:broadcast', owner=True)
            return ActionResult('succeeded')
        except TwitchFailure as exc:
            return ActionResult('unknown' if exc.uncertain else 'failed')
