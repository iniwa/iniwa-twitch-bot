"""Explicit production composition: one v2 recorder, retained legacy archives."""

import time

import config as c
from routes.application import create_app
from services.storage import load_stream_index, save_stream_index, fix_dangling_states
from services.twitch_api import sync_vod_history
from services.download import execute_download
from src.twitchbot.application.workers import PeriodicWorker


class ArchiveWorker:
    """Retain VOD sync/download ownership without the old IRC/viewer workers."""
    def __init__(self, container, configuration, *, clock=time.monotonic):
        self.container, self.configuration, self.clock = container, configuration, clock
        self.next_sync = 0
        self.current = None
        self.pending = {}

    def step(self):
        operations = self.container.operations
        if not operations.allowed() or not operations.client.status()['read']:
            return {'state': 'paused'}
        now = self.clock()
        stream = self.container.live_provider.snapshot().stream
        if stream.state == 'live' and not stream.stale and stream.id:
            # New recordings live in SQLite; the legacy index owns only VOD files.
            with c.file_lock:
                index = load_stream_index()
                entry = index.setdefault(stream.id, {'source': 'v2', 'vod_status': 'not_downloaded'})
                entry.update(start_time=stream.started_at, title=stream.title, game_name=stream.game)
                save_stream_index(index)
            self.current = stream.id
            self.pending.pop(stream.id, None)
        elif stream.state == 'offline' and self.current:
            if self.configuration().get('enable_vod_download'):
                self.pending[self.current] = now + 300
            self.current = None
        if now >= self.next_sync:
            sync_vod_history(self.configuration())
            self.next_sync = now + 1800
        for stream_id, due in tuple(self.pending.items()):
            if now >= due and operations.allowed():
                self.pending.pop(stream_id)
                if self.configuration().get('enable_vod_download'):
                    execute_download(self.configuration(), stream_id)
                break
        return {'state': 'running', 'pending_downloads': len(self.pending)}


def create_operational_app(container):
    """Called after explicit configuration selection, never from legacy startup."""
    client = container.operations.client
    def videos(first):
        return client._data(client.request('GET', 'videos', params={
            'user_id': client.channel_id, 'type': 'archive', 'first': first}))
    def configuration():
        legacy = c.load_config()
        # These dictionaries are transient, never passed to save_config.
        return {'broadcaster_id': client.channel_id,
                'enable_vod_download': bool(legacy.get('enable_vod_download')),
                '_videos_reader': videos}
    app = create_app(v2_container=container, primary=True,
                     channel_name=c.load_config().get('channel_name', ''))
    app.extensions['twitchbot.vod_configuration'] = configuration
    archive = ArchiveWorker(container, configuration)
    container.runtime.workers = (*container.runtime.workers,
                                PeriodicWorker('archives', archive.step, interval=20))
    container.runtime.recover = (*container.runtime.recover, fix_dangling_states)
    return app
