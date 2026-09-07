"""Explicit prediction previews and a single non-retrying worker dispatch."""

from datetime import timedelta
import json
from uuid import uuid4

from ..adapters.persistence.community import CommunityRepository
from ..adapters.persistence.sqlite import from_rfc3339, to_rfc3339
from ..adapters.twitch import TwitchFailure
from .analytics import identifier
from .chat_automation import text, number
from .persistence import PersistenceError


def prediction_spec(value):
    if not isinstance(value, dict) or set(value) != {'title', 'outcomes', 'prediction_window'} or not isinstance(value['outcomes'], list) or not 2 <= len(value['outcomes']) <= 10:
        raise PersistenceError('invalid_prediction', 'prediction')
    outcomes = [text(v, 25) for v in value['outcomes']]
    if len(set(outcomes)) != len(outcomes):
        raise PersistenceError('invalid_prediction', 'prediction')
    return dict(title=text(value['title'], 45), outcomes=outcomes, prediction_window=number(value['prediction_window'], 30, 1800))


class Predictions:
    def __init__(self, database, channel_id, client, live, *, running=lambda: False, clock):
        self.repository = CommunityRepository(database, channel_id, clock=clock)
        self.client, self.live, self.running, self.clock = client, live, running, clock
        self.state = 'not_checked'

    def _policy(self, c):
        row = c.execute('SELECT * FROM prediction_policy WHERE channel_id=?', (self.repository.channel_id,)).fetchone()
        return {'enabled': bool(row['enabled']), 'revision': row['revision']} if row else {'enabled': False, 'revision': 0}

    def snapshot(self, *, sort='name', order='asc'):
        if sort not in ('name', 'updated_at') or order not in ('asc', 'desc'):
            raise PersistenceError('invalid_sort', 'prediction')
        with self.repository.transaction() as c:
            row = c.execute('SELECT * FROM prediction_cache WHERE channel_id=?', (self.repository.channel_id,)).fetchone()
            items = json.loads(row['items_json']) if row else []
            fresh = bool(row and self.state == 'ready' and 0 <= (self.clock()-from_rfc3339(row['observed_at'])).total_seconds() <= 30)
            ordering = f'sort_name(p.name) {order.upper()},p.id {order.upper()}' if sort == 'name' else f'(t.updated_at IS NULL) ASC,t.updated_at {order.upper()},p.id {order.upper()}'
            return dict(policy=self._policy(c), items=items, observed_at=row['observed_at'] if row else None,
                        state=self.state if self.running() else 'paused', fresh=fresh and self.running(),
                        presets=[dict(id=r['id'], name=r['name'], revision=r['revision'], specification=json.loads(r['specification_json']), created_at=r['created_at'], updated_at=r['updated_at']) for r in c.execute(f'SELECT p.*,t.created_at,t.updated_at FROM prediction_presets p LEFT JOIN prediction_preset_times t ON t.channel_id=p.channel_id AND t.preset_id=p.id WHERE p.channel_id=? ORDER BY {ordering}', (self.repository.channel_id,))],
                        sort=sort, order=order,
                        operations=[dict(id=r['id'], action=r['action'], state=r['state'], result_code=r['result_code'], remote_id=r['remote_id']) for r in c.execute("SELECT * FROM prediction_operations WHERE channel_id=? AND state<>'preview' ORDER BY created_at DESC,id DESC LIMIT 30", (self.repository.channel_id,))])

    def save_policy(self, enabled, revision):
        if type(enabled) is not bool:
            raise PersistenceError('invalid_prediction_policy', 'prediction')
        number(revision, 0, 2**53-1)
        with self.repository.transaction(write=True) as c:
            if self._policy(c)['revision'] != revision:
                raise PersistenceError('revision_conflict', 'prediction')
            c.execute('INSERT INTO prediction_policy VALUES (?,?,?) ON CONFLICT(channel_id) DO UPDATE SET enabled=excluded.enabled,revision=excluded.revision', (self.repository.channel_id, int(enabled), revision+1))
        return {'enabled': enabled, 'revision': revision+1}

    def save_preset(self, key, name, spec, revision):
        key, name, spec = identifier(key), text(name, 80), prediction_spec(spec)
        number(revision, 0, 2**53-1)
        with self.repository.transaction(write=True) as c:
            old = c.execute('SELECT p.revision,t.created_at FROM prediction_presets p LEFT JOIN prediction_preset_times t ON t.channel_id=p.channel_id AND t.preset_id=p.id WHERE p.channel_id=? AND p.id=?', (self.repository.channel_id, key)).fetchone()
            if (old[0] if old else 0) != revision:
                raise PersistenceError('revision_conflict', 'prediction')
            if not old and c.execute('SELECT COUNT(*) FROM prediction_presets WHERE channel_id=?', (self.repository.channel_id,)).fetchone()[0] >= 100:
                raise PersistenceError('definition_limit', 'prediction')
            c.execute('INSERT INTO prediction_presets VALUES (?,?,?,?,?) ON CONFLICT(channel_id,id) DO UPDATE SET name=excluded.name,specification_json=excluded.specification_json,revision=excluded.revision', (self.repository.channel_id, key, name, json.dumps(spec), revision+1))
            now = to_rfc3339(self.clock())
            c.execute('INSERT INTO prediction_preset_times VALUES (?,?,?,?) ON CONFLICT(channel_id,preset_id) DO UPDATE SET updated_at=excluded.updated_at', (self.repository.channel_id, key, now, now))
        return dict(id=key, name=name, specification=spec, revision=revision+1, created_at=old['created_at'] if old else now, updated_at=now)

    def _allowed(self, c, action, payload, items, stream_id):
        if not self.running():
            raise PersistenceError('runtime_stopped', 'prediction')
        channel = self.repository.channel_id
        if action == 'start':
            live = self.live.snapshot().stream
            if not self._policy(c)['enabled'] or live.state != 'live' or live.stale or live.id != stream_id or not 0 <= (self.clock()-from_rfc3339(live.observed_at)).total_seconds() <= 30:
                raise PersistenceError('prediction_start_unavailable', 'prediction')
            if any(item['status'] in ('ACTIVE', 'LOCKED') for item in items) or c.execute("SELECT 1 FROM prediction_operations WHERE channel_id=? AND action='start' AND state='unknown'", (channel,)).fetchone():
                raise PersistenceError('prediction_already_active_or_unknown', 'prediction')
            preset = c.execute('SELECT revision FROM prediction_presets WHERE channel_id=? AND id=?', (channel, payload['preset_id'])).fetchone()
            if preset is None or preset[0] != payload['preset_revision']:
                raise PersistenceError('preview_changed', 'prediction')
        else:
            item = next((i for i in items if i['id'] == payload['id']), None)
            expected = ('ACTIVE',) if action == 'lock' else ('LOCKED',) if action == 'resolve' else ('ACTIVE', 'LOCKED')
            if item is None or item['status'] not in expected or item['title'] != payload['title']:
                raise PersistenceError('prediction_state_changed', 'prediction')
            if action == 'resolve' and not any(o['id'] == payload['winning_outcome_id'] and o['title'] == payload['winning_title'] for o in item['outcomes']):
                raise PersistenceError('prediction_outcome_changed', 'prediction')

    def preview(self, action, target, winning_outcome_id=None):
        if action not in ('start', 'lock', 'resolve', 'cancel'):
            raise PersistenceError('invalid_prediction_action', 'prediction')
        target = identifier(target)
        snapshot = self.snapshot()
        if not snapshot['fresh']:
            raise PersistenceError('prediction_state_unavailable', 'prediction')
        stream_id = self.live.snapshot().stream.id if action == 'start' else None
        if action == 'start':
            preset = next((p for p in snapshot['presets'] if p['id'] == target), None)
            if not preset:
                raise PersistenceError('preset_not_found', 'prediction')
            payload = dict(preset_id=target, preset_revision=preset['revision'], **preset['specification'])
        else:
            item = next((i for i in snapshot['items'] if i['id'] == target), None)
            if not item:
                raise PersistenceError('prediction_not_found', 'prediction')
            payload = dict(id=item['id'], title=item['title'])
            if action == 'resolve':
                outcome = next((o for o in item['outcomes'] if o['id'] == winning_outcome_id), None)
                if not outcome:
                    raise PersistenceError('invalid_prediction_outcome', 'prediction')
                payload.update(winning_outcome_id=outcome['id'], winning_title=outcome['title'])
        key, now = uuid4().hex, self.clock()
        with self.repository.transaction(write=True) as c:
            self._allowed(c, action, payload, snapshot['items'], stream_id)
            if action != 'start':
                original = c.execute("SELECT stream_id FROM prediction_operations WHERE channel_id=? AND action='start' AND remote_id=? AND state='succeeded'", (self.repository.channel_id, target)).fetchone()
                stream_id = original[0] if original else None
            c.execute('INSERT INTO prediction_operations VALUES (?,?,?,?,?,?,?,?,?,?,?)', (self.repository.channel_id, key, action, json.dumps(payload), stream_id, 'preview', None, 'awaiting_confirmation', to_rfc3339(now), to_rfc3339(now+timedelta(seconds=120)), None))
        return dict(id=key, action=action, content=payload, expires_at=to_rfc3339(now+timedelta(seconds=120)), refund=action == 'cancel')

    def confirm(self, key):
        key = identifier(key)
        with self.repository.transaction(write=True) as c:
            row = c.execute('SELECT * FROM prediction_operations WHERE channel_id=? AND id=?', (self.repository.channel_id, key)).fetchone()
            if not row:
                raise PersistenceError('preview_not_found', 'prediction')
            if row['state'] != 'preview':
                return {'id': key, 'state': row['state']}
            if not self.running() or from_rfc3339(row['expires_at']) <= self.clock():
                raise PersistenceError('preview_expired', 'prediction')
            if c.execute("SELECT 1 FROM prediction_operations WHERE channel_id=? AND state IN ('pending','dispatching')", (self.repository.channel_id,)).fetchone():
                raise PersistenceError('prediction_busy', 'prediction')
            c.execute("UPDATE prediction_operations SET state='pending',result_code='queued' WHERE channel_id=? AND id=?", (self.repository.channel_id, key))
        return {'id': key, 'state': 'pending'}

    def recover(self):
        with self.repository.transaction(write=True) as c:
            c.execute("UPDATE prediction_operations SET state=CASE WHEN state='dispatching' THEN 'unknown' ELSE 'expired' END,result_code='restart_requires_review',finished_at=? WHERE channel_id=? AND state IN ('pending','dispatching','preview')", (to_rfc3339(self.clock()), self.repository.channel_id))

    def paused(self):
        self.state = 'paused'
        with self.repository.transaction(write=True) as c:
            c.execute("UPDATE prediction_operations SET state='expired',result_code='runtime_stopped',finished_at=? WHERE channel_id=? AND state IN ('pending','preview')", (to_rfc3339(self.clock()), self.repository.channel_id))

    def step(self):
        if not self.running():
            self.state = 'paused'
            return {'state': self.state}
        try:
            self.client.validate()
            items = self.client.predictions()
            if not self.running():
                return {'state': 'paused'}
            channel, now = self.repository.channel_id, self.clock()
            with self.repository.transaction(write=True) as c:
                c.execute('INSERT INTO prediction_cache VALUES (?,?,?) ON CONFLICT(channel_id) DO UPDATE SET items_json=excluded.items_json,observed_at=excluded.observed_at', (channel, json.dumps(items), to_rfc3339(now)))
                # Known remote IDs can be reconciled to the requested final
                # state. An unknown create has no proven ID and stays unknown.
                unresolved = c.execute("SELECT * FROM prediction_operations WHERE channel_id=? AND state='unknown' AND action<>'start' AND remote_id IS NOT NULL", (channel,)).fetchall()
                for operation in unresolved:
                    desired = {'lock':'LOCKED','resolve':'RESOLVED','cancel':'CANCELED'}[operation['action']]
                    payload = json.loads(operation['payload_json'])
                    remote = next((i for i in items if i['id'] == operation['remote_id']), None)
                    if remote and remote['status'] == desired and (operation['action'] != 'resolve' or remote.get('winning_outcome_id') == payload['winning_outcome_id']):
                        c.execute("UPDATE prediction_operations SET state='succeeded',result_code='state_confirmed_on_refresh',finished_at=? WHERE channel_id=? AND id=?", (to_rfc3339(now), channel, operation['id']))
                row = c.execute("SELECT * FROM prediction_operations WHERE channel_id=? AND state='pending' ORDER BY created_at,id LIMIT 1", (channel,)).fetchone()
                if row:
                    payload = json.loads(row['payload_json'])
                    try:
                        if from_rfc3339(row['expires_at']) <= now:
                            raise PersistenceError('preview_expired', 'prediction')
                        self._allowed(c, row['action'], payload, items, row['stream_id'])
                    except PersistenceError as exc:
                        c.execute("UPDATE prediction_operations SET state='failed',result_code=?,finished_at=? WHERE channel_id=? AND id=?", (exc.code, to_rfc3339(now), channel, row['id']))
                        row = None
                    else:
                        c.execute("UPDATE prediction_operations SET state='dispatching',result_code='attempting' WHERE channel_id=? AND id=?", (channel, row['id']))
            self.state = 'ready'
            if row:
                remote_id, result, code = payload.get('id'), 'unknown', 'invalid_result'
                try:
                    remote = self.client.change_prediction(row['action'], payload)
                    remote_id, result, code = remote['id'], 'succeeded', 'confirmed'
                except TwitchFailure as exc:
                    result, code = ('unknown' if exc.uncertain else 'failed'), exc.code
                with self.repository.transaction(write=True) as c:
                    c.execute('UPDATE prediction_operations SET state=?,remote_id=?,result_code=?,finished_at=? WHERE channel_id=? AND id=?', (result, remote_id, code, to_rfc3339(self.clock()), channel, row['id']))
                    if result == 'succeeded':
                        items = [remote]+[i for i in items if i['id'] != remote_id]
                        c.execute('UPDATE prediction_cache SET items_json=?,observed_at=? WHERE channel_id=?', (json.dumps(items), to_rfc3339(self.clock()), channel))
                        # Keep the start-stream relationship when manually finishing
                        # after it ended; the event time remains the actual finish.
                        c.execute('INSERT OR IGNORE INTO channel_events VALUES (?,?,?,?,?,?,?,?,?)', (channel, 'prediction-operation-'+row['id'], 'prediction', None, to_rfc3339(self.clock()), to_rfc3339(self.clock()), row['stream_id'], 'stream' if row['stream_id'] else 'unknown', None))
                        self.repository._bump(c)
            return {'state': self.state}
        except TwitchFailure as exc:
            self.state = exc.code
            return {'state': self.state}
