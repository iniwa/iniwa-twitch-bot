"""Worker-owned device login, validated identity and serialized token rotation."""

from copy import deepcopy
from threading import Lock, RLock
import time

from ..adapters.twitch import TwitchCredentials, TwitchFailure
from .persistence import PersistenceError
from .workers import ProcessLease

BASE_SCOPES = ('moderator:read:followers', 'user:read:chat', 'channel:read:subscriptions',
               'bits:read', 'channel:read:redemptions', 'channel:manage:broadcast')


class DeviceLogin:
    def __init__(self, accounts, clients, store, api, *, owned=lambda: False,
                 recording=lambda: False, clock=time.time):
        if set(accounts)-{'broadcaster','bot'} or 'broadcaster' not in accounts:
            raise PersistenceError('invalid_oauth_configuration', 'login')
        self.accounts, self.clients, self.store, self.api = accounts, clients, store, api
        self.owned, self.recording, self.clock = owned, recording, clock
        self.lock, self.worker_lock = RLock(), Lock()
        self.lease = ProcessLease(store.root/'.oauth.lock')
        self.records, self.pending, self.next_check = {}, {}, {}
        self.ready = False
        self.states = {role: {'state':'not_connected'} for role in accounts}
        self.wake = lambda: None

    def recover(self):
        self.store.check(); self.lease.acquire()
        try:
            for role, account in self.accounts.items():
                record = self.store.load(role)
                for item in (record['grant'], record['candidate']):
                    if item and (item['client_id'], item['user_id']) != (account.client_id, account.user_id):
                        raise PersistenceError('oauth_identity_changed', 'login')
                self.records[role] = record
                if record['grant'] and not record['refresh_pending']:
                    self._apply(role, record['grant'])
                self.states[role] = {'state': 'authorization_required' if record['refresh_pending'] else 'validating' if record['candidate'] else 'saved' if record['grant'] else 'not_connected'}
            self.ready = True
        except Exception:
            self.lease.release(); raise

    def close(self):
        with self.lock:
            self.ready = False; self.pending.clear()
            for role in self.states: self.states[role] = {'state':'stopped'}
        self.lease.release()

    def authorized(self, role):
        # Called while the Helix lock is held: never acquire the login lock here.
        # Records are replaced atomically, not mutated after publication.
        record = self.records.get(role)
        return bool(self.ready and record and record['grant'] and not record['refresh_pending'])

    def snapshot(self):
        with self.lock:
            return {'configured': True, 'ready': self.ready, 'accounts': deepcopy(self.states)}

    def begin(self, role, *, predictions=False):
        if role not in self.accounts or type(predictions) is not bool or (role == 'bot' and predictions):
            raise PersistenceError('invalid_oauth_request', 'login')
        with self.lock:
            if not self.ready or not self.owned(): raise PersistenceError('oauth_runtime_unavailable', 'login')
            if role not in self.pending:
                scopes = list(BASE_SCOPES) if role == 'broadcaster' else ['user:write:chat']
                if predictions: scopes.append('channel:manage:predictions')
                self.pending[role] = {'scopes': scopes}
                self.states[role] = {'state':'starting'}
        self.wake()
        return {'state':'accepted'}

    def cancel(self, role):
        with self.lock:
            if role not in self.accounts: raise PersistenceError('invalid_oauth_request', 'login')
            if role not in self.pending: return {'state':'unchanged'}
            self.pending.pop(role, None)
            self.states[role] = {'state':'cancelled'}
        return {'state':'cancelled'}

    def _apply(self, role, grant):
        self.clients[role].replace_credentials(TwitchCredentials(grant['client_id'], grant['user_id'], grant['access_token']))

    def _save(self, role, record):
        self.store.save(role, record)
        self.records[role] = record

    def _candidate(self, role, data, scopes):
        if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not 1 <= len(data[k]) <= 4096 or any(c.isspace() for c in data[k]) for k in ('access_token', 'refresh_token')):
            raise TwitchFailure('oauth_invalid_response')
        if not isinstance(data.get('token_type'), str) or data['token_type'].lower() != 'bearer' or type(data.get('expires_in')) is not int or not 60 < data['expires_in'] <= 366*86400:
            raise TwitchFailure('oauth_invalid_response')
        account = self.accounts[role]
        return dict(client_id=account.client_id, user_id=account.user_id, access_token=data['access_token'],
                    refresh_token=data['refresh_token'], expires_at=self.clock()+data['expires_in'], requested_scopes=scopes)

    def _poll_login(self, role, attempt):
        now = self.clock()
        if 'device_code' not in attempt:
            result = self.api.begin(self.accounts[role], attempt['scopes'])
            with self.lock:
                if self.pending.get(role) is not attempt: return
                attempt.update(result, expires_at=now+result['expires_in'], next_poll=now+result['interval'])
                self.states[role] = {'state':'awaiting_login', 'user_code':result['user_code'],
                    'verification_uri':result['verification_uri'], 'expires_at':attempt['expires_at']}
            return
        if now >= attempt['expires_at']: raise TwitchFailure('oauth_code_expired')
        if now < attempt['next_poll']: return
        attempt['next_poll'] = now+attempt['interval']
        reply = self.api.exchange(self.accounts[role], attempt['scopes'], attempt['device_code'])
        data = reply.data if isinstance(reply.data, dict) else {}
        error = data.get('message', data.get('error'))
        if reply.status == 400 and error == 'authorization_pending': return
        if reply.status == 429 or (reply.status == 400 and error == 'slow_down'):
            attempt['interval'] = min(300, max(60 if reply.status == 429 else 5, attempt['interval']+5))
            attempt['next_poll'] = now+attempt['interval']; return
        if reply.status != 200: raise TwitchFailure('oauth_login_failed')
        candidate = self._candidate(role, data, attempt['scopes'])
        with self.lock:
            if self.pending.get(role) is not attempt or not self.owned(): return
            record = deepcopy(self.records[role]); record['candidate'] = candidate
            self._save(role, record)
            self.pending.pop(role, None)
            self.states[role] = {'state':'validating'}
            self.next_check[role] = 0

    def _validate_candidate(self, role, candidate):
        reply = self.api.validate(candidate['access_token'])
        data = reply.data if isinstance(reply.data, dict) else {}
        if reply.status != 200:
            raise TwitchFailure('oauth_validation_unavailable' if reply.status not in (401,403) else 'authorization_required')
        account = self.accounts[role]
        if (data.get('client_id'), data.get('user_id')) != (account.client_id, account.user_id):
            raise TwitchFailure('oauth_wrong_account')
        if not isinstance(data.get('scopes'), list) or any(not isinstance(s, str) for s in data['scopes']) or not set(candidate['requested_scopes']) <= set(data['scopes']):
            raise TwitchFailure('oauth_scope_required')
        if type(data.get('expires_in')) is not int or data['expires_in'] <= 60:
            raise TwitchFailure('authorization_required')
        with self.lock:
            if not self.owned(): return
            grant = dict(candidate, expires_at=self.clock()+data['expires_in'])
            self._save(role, {'grant':grant, 'candidate':None, 'refresh_pending':False})
            self._apply(role, grant)
            self.states[role] = {'state':'connected'}

    def step(self):
        if not self.ready or not self.owned(): return {'state':'paused'}
        if not self.worker_lock.acquire(blocking=False): return {'state':'busy'}
        try:
            for role, account in self.accounts.items():
                with self.lock:
                    attempt = self.pending.get(role)
                    record = deepcopy(self.records[role])
                try:
                    if attempt:
                        self._poll_login(role, attempt)
                    elif record['candidate'] and self.clock() >= self.next_check.get(role, 0):
                        self.next_check[role] = self.clock()+30
                        self._validate_candidate(role, record['candidate'])
                    elif self.recording() and record['grant'] and not record['candidate'] and not record['refresh_pending'] and (record['grant']['expires_at'] <= self.clock()+60 or self.clients[role].status()['state'] == 'authorization_required'):
                        with self.lock:
                            record['refresh_pending'] = True
                            self.records[role] = deepcopy(record)
                            self._save(role, record)  # durable single-use refresh intent
                            self.states[role] = {'state':'refreshing'}
                        reply = self.api.refresh(account, record['grant']['refresh_token'])
                        if reply.status != 200: raise TwitchFailure('authorization_required')
                        candidate = self._candidate(role, reply.data, record['grant']['requested_scopes'])
                        with self.lock:
                            record = dict(record, candidate=candidate)
                            self._save(role, record)
                            self.states[role] = {'state':'validating'}
                        self.next_check[role] = 0
                except (TwitchFailure, PersistenceError) as exc:
                    with self.lock:
                        if attempt and self.pending.get(role) is not attempt: continue
                        self.pending.pop(role, None)
                        if self.records[role]['candidate'] and exc.code != 'oauth_validation_unavailable':
                            failed = deepcopy(self.records[role]); failed['candidate'] = None
                            try: self._save(role, failed)
                            except PersistenceError: pass
                        self.states[role] = {'state':exc.code}
            return {'state':'ready'}
        finally:
            self.worker_lock.release()
