"""Local definitions, unique aliases, durable dispatch ledger and cooldowns."""

import json

from .community import CommunityRepository
from .sqlite import to_rfc3339
from ...application.analytics import identifier
from ...application.chat_automation import specification, text, number
from ...application.persistence import PersistenceError


class AutomationRepository(CommunityRepository):
    def policy(self, c=None):
        if c is None:
            with self.transaction() as c:
                return self.policy(c)
        row = c.execute('SELECT * FROM automation_policy WHERE channel_id=?', (self.channel_id,)).fetchone()
        return dict(commands_enabled=bool(row['commands_enabled']), posts_enabled=bool(row['posts_enabled']), ignored=json.loads(row['ignored_json']), revision=row['revision']) if row else dict(commands_enabled=False, posts_enabled=False, ignored=[], revision=0)

    def save_policy(self, commands_enabled, posts_enabled, ignored, revision):
        if type(commands_enabled) is not bool or type(posts_enabled) is not bool or not isinstance(ignored, list) or len(ignored) > 100 or any(not isinstance(n, str) or not n.isascii() or not n.isdigit() for n in ignored):
            raise PersistenceError('invalid_automation_policy', 'automation')
        number(revision, 0, 2**53-1)
        with self.transaction(write=True) as c:
            old = self.policy(c)
            if old['revision'] != revision:
                raise PersistenceError('revision_conflict', 'automation')
            c.execute('INSERT INTO automation_policy VALUES (?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET commands_enabled=excluded.commands_enabled,posts_enabled=excluded.posts_enabled,ignored_json=excluded.ignored_json,revision=excluded.revision', (self.channel_id, int(commands_enabled), int(posts_enabled), json.dumps(sorted(set(ignored))), revision+1))
            self.cancel_pending(c, 'policy_changed')
            c.execute('DELETE FROM post_waits WHERE channel_id=? AND held=0', (self.channel_id,))
            return self.policy(c)

    def definitions(self, c=None, *, sort="position", order="asc", enabled=None):
        if sort not in ("position", "name", "updated_at") or order not in ("asc", "desc"):
            raise PersistenceError('invalid_sort', 'automation')
        if enabled is not None and type(enabled) is not bool:
            raise PersistenceError('invalid_filter', 'automation')
        if c is None:
            with self.transaction() as c:
                return self.definitions(c, sort=sort, order=order, enabled=enabled)
        where, values = 'd.channel_id=?', [self.channel_id]
        if enabled is not None:
            where += ' AND d.enabled=?'
            values.append(int(enabled))
        if sort == 'position':
            ordering = 'd.position ASC,d.id ASC'
        elif sort == 'name':
            ordering = f'sort_name(d.name) {order.upper()},d.id {order.upper()}'
        else:
            ordering = f'(t.updated_at IS NULL) ASC,t.updated_at {order.upper()},d.id {order.upper()}'
        rows = c.execute(f'SELECT d.*,t.created_at,t.updated_at FROM automation_definitions d LEFT JOIN automation_definition_times t ON t.channel_id=d.channel_id AND t.definition_id=d.id WHERE {where} ORDER BY {ordering}', values).fetchall()
        return [dict(id=r['id'], kind=r['kind'], name=r['name'], enabled=bool(r['enabled']), specification=json.loads(r['specification_json']), revision=r['revision'], execution_revision=r['execution_revision'], position=r['position'], created_at=r['created_at'], updated_at=r['updated_at']) for r in rows]

    def save_definition(self, key, kind, name, enabled, spec, revision, position=0):
        key, name, spec = identifier(key), text(name, 80), specification(kind, spec)
        number(revision, 0, 2**53-1); number(position, 0, 1000)
        if type(enabled) is not bool:
            raise PersistenceError('invalid_automation_definition', 'automation')
        with self.transaction(write=True) as c:
            definitions = self.definitions(c)
            old = next((d for d in definitions if d['id'] == key), None)
            if (old['revision'] if old else 0) != revision or (old and old['kind'] != kind):
                raise PersistenceError('revision_conflict', 'automation')
            if not old and (len(definitions) >= 100 or enabled):
                raise PersistenceError('new_definition_must_be_disabled' if enabled else 'definition_limit', 'automation')
            if kind == 'command':
                for alias in [spec['trigger'], *spec['aliases']]:
                    found = c.execute('SELECT definition_id FROM command_aliases WHERE channel_id=? AND name=?', (self.channel_id, alias)).fetchone()
                    if found and found[0] != key:
                        raise PersistenceError('command_name_conflict', 'automation')
            changed = old is None or old['specification'] != spec or old['enabled'] != enabled
            execution = (old['execution_revision'] if old else 0)+int(changed)
            c.execute('INSERT INTO automation_definitions VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,specification_json=excluded.specification_json,revision=excluded.revision,execution_revision=excluded.execution_revision,position=excluded.position', (self.channel_id, key, kind, name, int(enabled), json.dumps(spec), revision+1, execution, position))
            now = to_rfc3339(self.clock())
            c.execute('INSERT INTO automation_definition_times VALUES (?,?,?,?) ON CONFLICT(channel_id,definition_id) DO UPDATE SET updated_at=excluded.updated_at', (self.channel_id, key, now, now))
            c.execute('DELETE FROM command_aliases WHERE channel_id=? AND definition_id=?', (self.channel_id, key))
            if kind == 'command':
                c.executemany('INSERT INTO command_aliases VALUES (?,?,?)', [(self.channel_id, n, key) for n in [spec['trigger'], *spec['aliases']]])
            if changed:
                self.cancel_pending(c, 'definition_changed', key)
                c.execute('DELETE FROM post_waits WHERE channel_id=? AND definition_id=?', (self.channel_id, key))
            return next(d for d in self.definitions(c) if d['id'] == key)

    def cancel_pending(self, c, reason, key=None):
        sql = "UPDATE chat_dispatches SET state='skipped',reason=?,finished_at=? WHERE channel_id=? AND state='pending'"
        values = [reason, to_rfc3339(self.clock()), self.channel_id]
        if key is not None:
            sql += ' AND definition_id=?'; values.append(key)
        c.execute(sql, values)

    def reset(self):
        with self.transaction(write=True) as c:
            self.cancel_pending(c, 'connection_reset')
            c.execute('DELETE FROM post_waits WHERE channel_id=? AND held=0', (self.channel_id,))

    def recover(self):
        with self.transaction(write=True) as c:
            c.execute("UPDATE chat_dispatches SET state='unknown',reason='restart_requires_review',finished_at=? WHERE channel_id=? AND state='dispatching'", (to_rfc3339(self.clock()), self.channel_id))
            c.execute("UPDATE post_waits SET held=1 WHERE channel_id=? AND definition_id IN (SELECT definition_id FROM chat_dispatches WHERE channel_id=? AND kind='post' AND state='unknown')", (self.channel_id, self.channel_id))
        self.reset()

    def snapshot(self, *, sort="name", order="asc", enabled=None):
        with self.transaction() as c:
            return dict(policy=self.policy(c), definitions=self.definitions(c, sort=sort, order=order, enabled=enabled), sort=sort, order=order, enabled=enabled,
                        results=[dict(r) for r in c.execute('SELECT id,definition_id,kind,state,reason,created_at,finished_at FROM chat_dispatches WHERE channel_id=? ORDER BY created_at DESC,id DESC LIMIT 50', (self.channel_id,))],
                        waits=[dict(r) for r in c.execute('SELECT definition_id,started_at,comments,held FROM post_waits WHERE channel_id=?', (self.channel_id,))])
