"""Polling recorder with immutable cached reads and complete follower syncing."""

from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from ..adapters.twitch import TwitchFailure
from ..adapters.persistence.sqlite import from_rfc3339
from .community import Follower, Person
from .live import LiveSnapshot, StreamSnapshot
from .persistence import PersistenceError


class Recorder:
    def __init__(self, client, repository, *, clock=lambda: datetime.now(timezone.utc), running=lambda: False):
        self.client, self.repository, self.clock, self.running = client, repository, clock, running
        self.lock = RLock()
        self._snapshot = LiveSnapshot()

    def snapshot(self):
        with self.lock:
            return self._snapshot

    def stopped(self):
        with self.lock:
            self.repository.stopped()
            self._snapshot = LiveSnapshot(stream=StreamSnapshot(state='unavailable'),
                generated_at=self.clock(), bot_enabled=False, bot_state='stopped')

    def resume(self):
        with self.lock:
            self.repository.run_id = uuid4().hex

    def event_transition(self, snapshot):
        with self.lock:
            if self.running() and snapshot.observed_at >= self._snapshot.stream.observed_at:
                self._snapshot = LiveSnapshot(stream=snapshot, generated_at=self.clock(),
                    bot_enabled=True, bot_state='running', connections={'twitch': 'healthy', 'eventsub': 'healthy'})

    def step(self):
        if not self.running():
            return {'state': 'paused'}
        try:
            self.client.validate()
            stream = self.client.stream()
            channel = self.client.channel()
            if not self.running():
                return {'state': 'paused'}
            at = self.clock()
            with self.lock:
                if not self.running():
                    return {'state': 'paused'}
                snapshot = self.repository.save(stream, channel, at)
                live = LiveSnapshot(stream=snapshot, generated_at=at, bot_enabled=True,
                                    bot_state='running', connections={'twitch': 'healthy', 'helix': 'healthy'})
                self._snapshot = live
            return {'state': snapshot.state}
        except (TwitchFailure, PersistenceError) as exc:
            at, old = self.clock(), self.snapshot()
            try:
                self.repository.gap(old.stream.id, at)
            except PersistenceError:
                pass
            stale = replace(old.stream, state='degraded', stale=True)
            with self.lock:
                if not self.running():
                    return {'state': 'paused'}
                self._snapshot = LiveSnapshot(stream=stale, generated_at=at, bot_enabled=True,
                    bot_state='action_required' if 'authorization' in exc.code else 'running',
                    connections={'twitch': 'action_required' if 'authorization' in exc.code else 'degraded', 'helix': 'degraded'})
            return {'state': 'degraded', 'error': exc.code}


class FollowerSynchronizer:
    """One page per step; a failed/incomplete snapshot never removes followers."""
    def __init__(self, client, repository, *, running=lambda: False, interval_seconds=900):
        self.client, self.repository, self.running = client, repository, running
        self.interval = interval_seconds
        self.sync_id, self.cursor, self.next_sync = None, '', None
        self.pages = 0

    def step(self):
        if not self.running():
            return {'state': 'paused'}
        now = self.repository.clock()
        if self.next_sync and now < self.next_sync:
            return {'state': 'waiting'}
        if not self.client.status()['followers']:
            return {'state': 'authorization_required'}
        try:
            page = self.client.followers_page(self.cursor)
            if not self.running():
                return {'state': 'paused'}
            if self.sync_id is None:
                self.sync_id = uuid4().hex
                self.repository.start_sync(self.sync_id, page['total'])
            followers = [Follower(Person(row['user_id'], row.get('user_login'), row.get('user_name')),
                                  from_rfc3339(row['followed_at'])) for row in page['data']]
            cursor = page.get('pagination', {}).get('cursor') or None
            self.repository.append_sync_page(self.sync_id, followers, cursor=self.cursor, next_cursor=cursor, total=page['total'])
            self.pages += 1
            if cursor:
                if self.pages >= 1000:
                    raise PersistenceError('incomplete_sync', 'community')
                self.cursor = cursor
                return {'state': 'collecting'}
            state = self.repository.finish_sync(self.sync_id, success=True)
            self._reset(now)
            return {'state': state}
        except (TwitchFailure, PersistenceError, KeyError, TypeError, ValueError):
            if self.sync_id:
                self.repository.finish_sync(self.sync_id, success=False)
            self._reset(now)
            return {'state': 'failed'}

    def _reset(self, now):
        from datetime import timedelta
        self.sync_id, self.cursor, self.pages = None, '', 0
        self.next_sync = now+timedelta(seconds=self.interval)
