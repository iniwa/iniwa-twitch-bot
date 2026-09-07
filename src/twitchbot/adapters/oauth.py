"""Twitch device login and explicit private storage; no discovery or startup IO."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .twitch import HttpReply, RequestsTransport, TwitchFailure
from ..application.persistence import PersistenceError


@dataclass(frozen=True)
class OAuthAccount:
    client_id: str = field(repr=False)
    user_id: str = field(repr=False)
    client_type: str = 'public'
    client_secret: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.client_id, str) or not re.fullmatch(r'[A-Za-z0-9]{1,128}', self.client_id):
            raise PersistenceError('invalid_oauth_configuration', 'login')
        if not isinstance(self.user_id, str) or not re.fullmatch(r'[0-9]{1,32}', self.user_id):
            raise PersistenceError('invalid_oauth_configuration', 'login')
        if self.client_type not in ('public', 'confidential') or (self.client_type == 'confidential' and not self.client_secret):
            raise PersistenceError('invalid_oauth_configuration', 'login')
        if self.client_secret is not None and (not isinstance(self.client_secret, str) or not re.fullmatch(r'[A-Za-z0-9]{1,256}', self.client_secret)):
            raise PersistenceError('invalid_oauth_configuration', 'login')


class PrivateGrantStore:
    """A dedicated, pre-existing private directory, outside backup staging."""
    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise PersistenceError('invalid_oauth_directory', 'login')

    def check(self):
        if not self.root.is_dir() or self.root.is_symlink() or self.root.resolve() != self.root:
            raise PersistenceError('invalid_oauth_directory', 'login')
        if os.name == 'posix' and self.root.stat().st_mode & 0o077:
            raise PersistenceError('oauth_directory_not_private', 'login')

    def path(self, role):
        self.check()
        if role not in ('broadcaster', 'bot'):
            raise PersistenceError('invalid_oauth_role', 'login')
        path = self.root/(role+'.json')
        if path.is_symlink():
            raise PersistenceError('invalid_oauth_file', 'login')
        return path

    def load(self, role):
        path = self.path(role)
        if not path.exists():
            return {'grant': None, 'candidate': None, 'refresh_pending': False}
        try:
            if not path.is_file() or path.stat().st_size > 65536 or (os.name == 'posix' and path.stat().st_mode & 0o077):
                raise ValueError
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or set(value) != {'grant', 'candidate', 'refresh_pending'} or type(value['refresh_pending']) is not bool:
                raise ValueError
            for item in (value['grant'], value['candidate']):
                if item is None: continue
                if not isinstance(item, dict) or set(item) != {'client_id', 'user_id', 'access_token', 'refresh_token', 'expires_at', 'requested_scopes'}:
                    raise ValueError
                if any(not isinstance(item[k], str) or not item[k] or len(item[k]) > 4096 for k in ('client_id','user_id','access_token','refresh_token')):
                    raise ValueError
                if type(item['expires_at']) not in (int, float) or not 0 < item['expires_at'] < 10**12:
                    raise ValueError
                if not isinstance(item['requested_scopes'], list) or len(item['requested_scopes']) > 30 or any(not isinstance(s, str) or len(s)>100 for s in item['requested_scopes']):
                    raise ValueError
            return value
        except (OSError, ValueError, TypeError):
            raise PersistenceError('invalid_oauth_file', 'login') from None

    def save(self, role, value):
        path = self.path(role)
        temporary = self.root/('.oauth-'+uuid4().hex)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(value, output, ensure_ascii=True, allow_nan=False)
                output.flush(); os.fsync(output.fileno())
            self.check()
            if path.is_symlink(): raise PersistenceError('invalid_oauth_file', 'login')
            os.replace(temporary, path)
            if os.name == 'posix':
                descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        except (OSError, ValueError, TypeError):
            raise PersistenceError('oauth_save_failed', 'login') from None
        finally:
            temporary.unlink(missing_ok=True)


class TwitchOAuth:
    def __init__(self, transport=None):
        self.transport = transport or RequestsTransport()

    def call(self, endpoint, data):
        try:
            reply = self.transport('POST', 'https://id.twitch.tv/oauth2/'+endpoint, data=data)
            if not isinstance(reply, HttpReply): raise TwitchFailure('oauth_unavailable')
            return reply
        except Exception:
            raise TwitchFailure('oauth_unavailable', uncertain=True) from None

    def begin(self, account, scopes):
        reply = self.call('device', {'client_id': account.client_id, 'scopes': ' '.join(scopes)})
        data = reply.data
        if reply.status != 200 or not isinstance(data, dict): raise TwitchFailure('oauth_begin_failed')
        try:
            if any(not isinstance(data[k], str) or not 1 <= len(data[k]) <= 4096 for k in ('device_code','user_code','verification_uri')):
                raise ValueError
            uri = urlsplit(data['verification_uri'])
            if uri.scheme != 'https' or uri.netloc != 'www.twitch.tv' or uri.path != '/activate' or uri.fragment:
                raise ValueError
            if not re.fullmatch(r'[A-Za-z0-9-]{4,32}', data['user_code']): raise ValueError
            if type(data['expires_in']) is not int or not 1 <= data['expires_in'] <= 3600: raise ValueError
            if type(data.get('interval', 5)) is not int or not 1 <= data.get('interval', 5) <= 300: raise ValueError
            return {k: data[k] for k in ('device_code','user_code','verification_uri','expires_in')} | {'interval': max(5, data.get('interval', 5))}
        except (KeyError, ValueError, TypeError):
            raise TwitchFailure('oauth_invalid_response') from None

    def exchange(self, account, scopes, device_code):
        return self.call('token', {'client_id': account.client_id, 'scopes': ' '.join(scopes),
            'device_code': device_code, 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'})

    def refresh(self, account, refresh_token):
        data = {'client_id': account.client_id, 'refresh_token': refresh_token, 'grant_type': 'refresh_token'}
        if account.client_type == 'confidential': data['client_secret'] = account.client_secret
        return self.call('token', data)

    def validate(self, token):
        try:
            reply = self.transport('GET', 'https://id.twitch.tv/oauth2/validate', headers={'Authorization':'OAuth '+token})
            if not isinstance(reply, HttpReply): raise ValueError
            return reply
        except Exception:
            raise TwitchFailure('oauth_validation_unavailable') from None
