"""One dispatch queue for commands and scheduled posts; never retry unknown sends."""

from datetime import timedelta
from threading import RLock
from uuid import uuid4

from ..adapters.persistence.sqlite import from_rfc3339, to_rfc3339
from ..adapters.twitch import TwitchFailure
from .chat_automation import ROLES, render_command, specification
from .persistence import PersistenceError


class ChatWorker:
    def __init__(self, repository, live, client=None, *, running=lambda: False, connected=lambda: False):
        self.repository, self.live, self.client = repository, live, client
        self.running, self.connected = running, connected
        self.lock = RLock()
        self._state, self._active = 'paused', False
        self._blocked_until = repository.clock()
        self._shared_checked_at = None

    def snapshot(self, *, sort='name', order='asc', enabled=None):
        result = self.repository.snapshot(sort=sort, order=order, enabled=enabled)
        # A string assignment is the publication boundary; a UI query must not
        # wait on the worker's network/dispatch lock.
        result['state'] = self._state
        result['sender_configured'] = self.client is not None
        return result

    def _stream(self):
        stream, now = self.live.snapshot().stream, self.repository.clock()
        return stream if self.running() and self.connected() and stream.state == 'live' and not stream.stale and stream.id and 0 <= (now-from_rfc3339(stream.observed_at)).total_seconds() <= 30 else None

    def reset(self):
        with self.lock:
            self.repository.reset()
            self._active, self._state = False, 'paused'
            self._shared_checked_at = None

    def preview(self, spec, input_text, role):
        spec = specification('command', spec)
        if not isinstance(input_text, str) or len(input_text) > 500 or not isinstance(role, str) or role not in ROLES:
            raise PersistenceError('invalid_command_preview', 'automation')
        matches = input_text.strip().lower() in [spec['trigger'], *spec['aliases']]
        permitted = ROLES[role] >= ROLES[spec['role']]
        stream = self.live.snapshot().stream
        reply = render_command(spec, stream, self.repository.clock(), self.repository.definitions(), ROLES[role]) if matches and permitted else None
        return {'matched': matches, 'permitted': permitted, 'response': reply, 'sent': False}

    def on_chat(self, event, stream_id, occurred):
        """Called only for a newly recorded chat, after its storage transaction."""
        with self.lock:
            stream = self._stream()
            now, channel = self.repository.clock(), self.repository.channel_id
            user = event.get('chatter_user_id')
            if not stream or stream.id != stream_id or self.client is None or user == self.client.credentials.user_id or event.get('source_broadcaster_user_id') not in (None, '', channel) or not 0 <= (now-occurred).total_seconds() <= 15:
                return
            # Badges belong to the authenticated EventSub channel. Unknown badge
            # shapes never grant restricted roles; VIP is not a subscriber.
            role = 3 if user == channel else 0
            badges = event.get('badges')
            if isinstance(badges, list):
                names = {b.get('set_id') for b in badges if isinstance(b, dict) and isinstance(b.get('set_id'), str)}
                role = max(role, 2 if 'moderator' in names else 1 if names & {'subscriber', 'founder'} else 0)
            key, body = event.get('message_id'), event.get('message', {}).get('text')
            if not isinstance(key, str) or not isinstance(body, str) or not isinstance(user, str):
                return
            encoded = to_rfc3339(now)
            with self.repository.transaction(write=True) as c:
                policy = self.repository.policy(c)
                if user in policy['ignored'] or c.execute('SELECT 1 FROM automation_messages WHERE channel_id=? AND id=?', (channel, key)).fetchone():
                    return
                c.execute('INSERT INTO automation_messages VALUES (?,?,?)', (channel, key, encoded))
                definitions = self.repository.definitions(c)
                definition = next((d for d in definitions if d['kind'] == 'command' and body.strip().lower() in [d['specification']['trigger'], *d['specification']['aliases']]), None)
                if definition is None:
                    if policy['posts_enabled']:
                        c.execute('UPDATE post_waits SET comments=comments+1 WHERE channel_id=? AND stream_id=? AND started_at<=? AND held=0', (channel, stream.id, to_rfc3339(occurred)))
                    return
                if not policy['commands_enabled'] or not definition['enabled'] or role < ROLES[definition['specification']['role']]:
                    return
                spec, ident = definition['specification'], definition['id']
                c.execute("UPDATE chat_dispatches SET state='skipped',reason='expired',finished_at=? WHERE channel_id=? AND state='pending' AND expires_at<=?", (encoded, channel, encoded))
                if c.execute("SELECT COUNT(*) FROM chat_dispatches WHERE channel_id=? AND state='pending'", (channel,)).fetchone()[0] >= 20:
                    return
                if c.execute('SELECT 1 FROM command_cooldowns WHERE channel_id=? AND definition_id=? AND user_id IN (?,?) AND available_at>?', (channel, ident, '', user, encoded)).fetchone():
                    return
                for who, seconds in (('', spec['shared_seconds']), (user, spec['user_seconds'])):
                    c.execute('INSERT INTO command_cooldowns VALUES (?,?,?,?) ON CONFLICT(channel_id,definition_id,user_id) DO UPDATE SET available_at=excluded.available_at', (channel, ident, who, to_rfc3339(now+timedelta(seconds=seconds))))
                c.execute('INSERT INTO chat_dispatches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (channel, uuid4().hex, ident, definition['execution_revision'], 'command', stream.id, user, role, key, 'pending', 'queued', encoded, to_rfc3339(occurred+timedelta(seconds=15)), None))

    def _prepare(self, c, stream, now):
        channel, encoded = self.repository.channel_id, to_rfc3339(now)
        policy, definitions = self.repository.policy(c), self.repository.definitions(c)
        c.execute("UPDATE chat_dispatches SET state='skipped',reason='expired',finished_at=? WHERE channel_id=? AND state='pending' AND (expires_at<=? OR stream_id<>?)", (encoded, channel, encoded, stream.id))
        category_row = c.execute('SELECT game_id FROM channel_read_model WHERE channel_id=?', (channel,)).fetchone()
        category = category_row[0] if category_row else None
        posts = [d for d in definitions if d['kind'] == 'post' and d['enabled'] and policy['posts_enabled']]
        specific = any(d['specification']['target'] == 'category' and d['specification']['category_id'] == category for d in posts)
        eligible = []
        for d in posts:
            spec = d['specification']
            target = spec['target'] == 'all' or (spec['target'] == 'category' and spec['category_id'] == category) or (spec['target'] == 'default' and not specific)
            wait = c.execute('SELECT * FROM post_waits WHERE channel_id=? AND definition_id=?', (channel, d['id'])).fetchone()
            if wait and wait['held']:
                continue
            if not target:
                c.execute('DELETE FROM post_waits WHERE channel_id=? AND definition_id=?', (channel, d['id']))
                continue
            if wait is None or wait['stream_id'] != stream.id or wait['execution_revision'] != d['execution_revision'] or (spec['target'] != 'all' and wait['category_id'] != category):
                c.execute('INSERT INTO post_waits VALUES (?,?,?,?,?,?,0,0) ON CONFLICT(channel_id,definition_id) DO UPDATE SET execution_revision=excluded.execution_revision,stream_id=excluded.stream_id,category_id=excluded.category_id,started_at=excluded.started_at,comments=0', (channel, d['id'], d['execution_revision'], stream.id, category, encoded))
            elif (now-from_rfc3339(wait['started_at'])).total_seconds() >= spec['minutes']*60 and wait['comments'] >= spec['comments']:
                eligible.append((wait['started_at'], d['position'], d['id'], d))
        # Reserve across restarts using the durable dispatch timestamp, including
        # failures and uncertain outcomes. There is no catch-up burst.
        latest = c.execute("SELECT MAX(created_at) FROM chat_dispatches WHERE channel_id=? AND state IN ('dispatching','sent','failed','unknown')", (channel,)).fetchone()[0]
        if latest and (now-from_rfc3339(latest)).total_seconds() < 2:
            return None
        row = c.execute("SELECT * FROM chat_dispatches WHERE channel_id=? AND state='pending' ORDER BY created_at,id LIMIT 1", (channel,)).fetchone()
        if row:
            d = next((d for d in definitions if d['id'] == row['definition_id']), None)
            if not policy['commands_enabled'] or not d or not d['enabled'] or d['execution_revision'] != row['definition_revision']:
                c.execute("UPDATE chat_dispatches SET state='skipped',reason='definition_changed',finished_at=? WHERE channel_id=? AND id=?", (encoded, channel, row['id']))
                return None
            reply = render_command(d['specification'], stream, now, definitions, row['role'])
            c.execute("UPDATE chat_dispatches SET state='dispatching',created_at=?,reason='attempting' WHERE channel_id=? AND id=?", (encoded, channel, row['id']))
            return row['id'], d['id'], 'command', reply
        last_post = c.execute("SELECT MAX(created_at) FROM chat_dispatches WHERE channel_id=? AND kind='post' AND state IN ('dispatching','sent','failed','unknown')", (channel,)).fetchone()[0]
        if not eligible or (last_post and (now-from_rfc3339(last_post)).total_seconds() < 60):
            return None
        d = min(eligible, key=lambda e:e[:3])[3]
        key = uuid4().hex
        c.execute('INSERT INTO chat_dispatches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (channel, key, d['id'], d['execution_revision'], 'post', stream.id, None, None, None, 'dispatching', 'attempting', encoded, encoded, None))
        return key, d['id'], 'post', d['specification']['body']

    def step(self):
        with self.lock:
            stream, now = self._stream(), self.repository.clock()
            policy = self.repository.policy()
            if not stream or not self.client or not (policy['commands_enabled'] or policy['posts_enabled']):
                if self._active:
                    self.reset()
                self._state = 'sender_unavailable' if self.client is None else 'paused'
                return {'state': self._state}
            if now < self._blocked_until:
                return {'state': self._state}
            try:
                self.client.validate()
                if 'user:write:chat' not in self.client.scopes:
                    raise TwitchFailure('authorization_scope_required')
                # User-token messages propagate throughout Shared Chat. Refuse
                # automatic sends while a session is active or cannot be checked.
                if self._shared_checked_at is None or (now-self._shared_checked_at).total_seconds() >= 10:
                    if self.client.shared_chat_active():
                        self.reset(); self._state = 'shared_chat_paused'
                        self._blocked_until = now+timedelta(seconds=10)
                        return {'state': self._state}
                    self._shared_checked_at = now
                self._active = True
                with self.repository.transaction(write=True) as c:
                    item = self._prepare(c, stream, now)
                if item is None:
                    self._state = 'waiting'
                    return {'state': self._state}
                key, definition_id, kind, body = item
                latest = self._stream()
                if not latest or latest.id != stream.id:
                    outcome, reason = 'skipped', 'stream_changed'
                else:
                    try:
                        outcome, reason = self.client.send_chat(body), 'twitch_result'
                    except TwitchFailure as exc:
                        outcome, reason = ('unknown' if exc.uncertain else 'failed'), exc.code
                        self._blocked_until = now+timedelta(seconds=60)
                with self.repository.transaction(write=True) as c:
                    c.execute('UPDATE chat_dispatches SET state=?,reason=?,finished_at=? WHERE channel_id=? AND id=?', (outcome, reason, to_rfc3339(self.repository.clock()), self.repository.channel_id, key))
                    if kind == 'post':
                        c.execute('UPDATE post_waits SET started_at=?,comments=0,held=? WHERE channel_id=? AND definition_id=?', (to_rfc3339(self.repository.clock()), int(outcome in ('unknown', 'failed')), self.repository.channel_id, definition_id))
                self._state = outcome
            except TwitchFailure as exc:
                self.reset(); self._state = exc.code
                self._blocked_until = now+timedelta(seconds=30)
            return {'state': self._state}
