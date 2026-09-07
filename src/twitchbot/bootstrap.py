"""Explicit operational composition; no import/factory-triggered worker startup."""

from datetime import datetime, timezone
import json
from pathlib import Path

from .adapters.nas import MountedNasTransfer
from .adapters.twitch import HelixClient, TwitchCredentials
from .adapters.eventsub import EventSubSession, WebSocketConnection
from .adapters.oauth import OAuthAccount, PrivateGrantStore, TwitchOAuth
from .application.login import DeviceLogin
from .adapters.persistence import SQLiteDatabase, SettingsRepository
from .adapters.persistence.analytics import HistoryReader
from .adapters.persistence.backups import BackupService
from .adapters.persistence.community import CommunityRepository
from .adapters.persistence.control import ControlRepository
from .adapters.persistence.maintenance import MaintenanceRepository
from .adapters.persistence.recording import RecordingRepository
from .adapters.persistence.automation import AutomationRepository
from .application.chat_worker import ChatWorker
from .application.predictions import Predictions
from .application.backup_worker import BackupWorker
from .application.live_actions import LiveActions
from .application.event_recording import EventRecorder
from .application.operations import Operations
from .application.persistence import PersistenceError
from .application.recorder import Recorder, FollowerSynchronizer
from .application.workers import ProcessLease, PeriodicWorker, WorkerRuntime
from .container import Container


def build_container(database_path, staging_root, credentials, channel_id, *,
                    nas_root=None, nas_source=None, transport=None, backup_limits=None,
                    eventsub_connect=WebSocketConnection, bot_credentials=None, bot_transport=None,
                    oauth_configuration=None, oauth_transport=None):
    """Prepare a new v2 composition using explicit paths. Caller starts runtime."""
    database = SQLiteDatabase(database_path)
    if not database.path.parent.is_dir() or database.path.parent.resolve() != database.path.parent:
        raise PersistenceError('database_parent_unavailable', 'bootstrap')
    staging = Path(staging_root)
    if not staging.is_absolute() or not staging.is_dir() or staging.resolve() != staging:
        raise PersistenceError('backup_area_unavailable', 'bootstrap')
    if bool(nas_root) != bool(nas_source):
        raise PersistenceError('invalid_nas_destination', 'bootstrap')
    database.migrate()
    clock = lambda: datetime.now(timezone.utc)
    runtime = WorkerRuntime(ProcessLease(database.path.parent/'.v2-runtime.lock'))
    owned = {}
    allowed = lambda: 'operations' in owned and owned['operations'].allowed()
    accounts = {}
    if oauth_configuration is not None:
        if not isinstance(oauth_configuration, dict) or set(oauth_configuration) != {'private_root', 'accounts'} or not isinstance(oauth_configuration['accounts'], dict) or not {'broadcaster'} <= oauth_configuration['accounts'].keys() <= {'broadcaster', 'bot'}:
            raise PersistenceError('invalid_oauth_configuration', 'bootstrap')
        try:
            accounts = {role: OAuthAccount(**value) for role, value in oauth_configuration['accounts'].items()}
        except (TypeError, ValueError):
            raise PersistenceError('invalid_oauth_configuration', 'bootstrap') from None
        if accounts['broadcaster'].user_id != channel_id or credentials is not None or bot_credentials is not None:
            raise PersistenceError('invalid_oauth_configuration', 'bootstrap')
        credentials = TwitchCredentials(accounts['broadcaster'].client_id, channel_id, 'pending-authorization')
        if 'bot' in accounts:
            bot_credentials = TwitchCredentials(accounts['bot'].client_id, accounts['bot'].user_id, 'pending-authorization')
    account_allowed = lambda role: allowed() and (not accounts or ('login' in owned and owned['login'].authorized(role)))
    client = HelixClient(credentials, channel_id, transport=transport, allowed=lambda: account_allowed('broadcaster'))
    recording = RecordingRepository(database, channel_id, clock=clock)
    recorder = Recorder(client, recording, running=allowed, clock=clock)
    community = CommunityRepository(database, channel_id, clock=clock)
    follower_sync = FollowerSynchronizer(client, community, running=allowed)
    bot = HelixClient(bot_credentials, channel_id, transport=bot_transport, allowed=lambda: account_allowed('bot')) if bot_credentials else None
    automation_repository = AutomationRepository(database, channel_id, clock=clock)
    chat = ChatWorker(automation_repository, recorder, bot, running=allowed,
                      connected=lambda: events.state == 'connected')
    events = EventSubSession(client, EventRecorder(community, publish_transition=recorder.event_transition,
        on_chat=chat.on_chat, on_gap=chat.reset), running=allowed, connect=eventsub_connect)
    controls = ControlRepository(database, channel_id, clock=clock)
    actions = LiveActions(controls, recorder, adapter=client, runtime_allowed=allowed)
    transfer = MountedNasTransfer(nas_root, nas_source, cancelled=lambda: not allowed()) if nas_root else None
    service = BackupService(database, staging, transfer=transfer, limits=backup_limits)
    maintenance = MaintenanceRepository(database, channel_id)
    backups = BackupWorker(service, maintenance, running=allowed)
    settings = SettingsRepository(database)
    operations = Operations(runtime, settings, maintenance, backups, client, recorder)
    owned['operations'] = operations
    predictions = Predictions(database, channel_id, client, recorder, running=allowed, clock=clock)
    operations.paused = (chat.reset, predictions.paused)
    runtime.workers = (PeriodicWorker('recording', recorder.step, interval=20),
                       PeriodicWorker('events', events.step, interval=.1),
                       PeriodicWorker('followers', follower_sync.step, interval=5),
                       PeriodicWorker('backups', backups.step, interval=15),
                       PeriodicWorker('chat', chat.step, interval=1),
                       PeriodicWorker('predictions', predictions.step, interval=20))
    runtime.recover = (recording.recover, controls.recover_interrupted, maintenance.recover, automation_repository.recover, predictions.recover)
    runtime.stopped = (recorder.stopped, events.close, chat.reset)
    login = None
    if accounts:
        store = PrivateGrantStore(oauth_configuration['private_root'])
        if store.root == database.path.parent or store.root.is_relative_to(staging) or staging.is_relative_to(store.root):
            raise PersistenceError('invalid_oauth_directory', 'bootstrap')
        login = DeviceLogin(accounts, {'broadcaster': client, **({'bot':bot} if bot else {})}, store,
            TwitchOAuth(oauth_transport), owned=lambda: runtime.snapshot().ready, recording=allowed)
        owned['login'] = login
        auth_worker = PeriodicWorker('login', login.step, interval=1)
        login.wake = auth_worker.wake
        runtime.workers = (auth_worker, *runtime.workers)
        runtime.recover = (*runtime.recover, login.recover)
        runtime.stopped = (*runtime.stopped, login.close)
    return Container(runtime=runtime, settings=operations.settings.settings, live_provider=recorder,
        history_reader=HistoryReader(database), community=community, live_actions=actions, operations=operations, automation=chat, predictions=predictions, login=login)


def _read_explicit_json(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise PersistenceError('configuration_unavailable', 'bootstrap')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise PersistenceError('configuration_unavailable', 'bootstrap') from None


def from_file(path):
    """Read only explicitly selected configuration files; never search for tokens.

    The runtime configuration holds database/staging/NAS paths and a separate
    credentials_file or oauth_file. Neither is copied into SQLite or backups.
    """
    config = _read_explicit_json(path)
    required = {'database_path', 'staging_root', 'channel_id'}
    if not required <= config.keys() or config.keys()-(required | {'credentials_file', 'oauth_file', 'nas_root', 'nas_source', 'bot_credentials_file'}):
        raise PersistenceError('invalid_configuration', 'bootstrap')
    if 'oauth_file' in config:
        if 'credentials_file' in config or 'bot_credentials_file' in config:
            raise PersistenceError('invalid_configuration', 'bootstrap')
        oauth = _read_explicit_json(config.pop('oauth_file'))
        return build_container(credentials=None, oauth_configuration=oauth, **config)
    if 'credentials_file' not in config:
        raise PersistenceError('invalid_configuration', 'bootstrap')
    secret = _read_explicit_json(config.pop('credentials_file'))
    if set(secret) != {'client_id', 'user_id', 'access_token'}:
        raise PersistenceError('invalid_credentials_file', 'bootstrap')
    credentials = TwitchCredentials(**secret)
    bot_file = config.pop('bot_credentials_file', None)
    bot_secret = _read_explicit_json(bot_file) if bot_file else None
    if bot_secret is not None and set(bot_secret) != {'client_id', 'user_id', 'access_token'}:
        raise PersistenceError('invalid_credentials_file', 'bootstrap')
    return build_container(credentials=credentials, bot_credentials=TwitchCredentials(**bot_secret) if bot_secret else None, **config)
