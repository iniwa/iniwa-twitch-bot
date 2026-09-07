"""Normalize selected EventSub events; never store raw payloads or duplicate chat."""

from uuid import uuid4

from ..adapters.persistence.sqlite import from_rfc3339, to_rfc3339
from .community import ChannelEvent, ChatMessage, Person
from .analytics import identifier
from .live import StreamSnapshot
from .persistence import PersistenceError


class EventRecorder:
    def __init__(self, repository, *, publish_transition=lambda snapshot: None, on_chat=lambda event, stream_id, at: None, on_gap=lambda: None):
        self.repository = repository
        self.publish_transition = publish_transition
        self.on_chat, self.on_gap = on_chat, on_gap

    def _stream_transition(self, kind, event, at):
        encoded, channel = to_rfc3339(at), self.repository.channel_id
        with self.repository.transaction(write=True) as c:
            if kind == 'stream.online':
                key, start = identifier(event['id']), from_rfc3339(event['started_at'])
                if start > at:
                    raise ValueError
                cached = c.execute('SELECT * FROM channel_read_model WHERE channel_id=?', (channel,)).fetchone()
                title = cached['title'] if cached and cached['title'] else '配信情報の取得待ち'
                game = cached['game_name'] if cached else None
                old = c.execute('SELECT channel_id,started_at,ended_at FROM streams WHERE id=?', (key,)).fetchone()
                if old and (old['channel_id'] != channel or old['started_at'] != to_rfc3339(start)):
                    raise PersistenceError('stream_identity_conflict', 'eventsub')
                if old and old['ended_at'] is not None:
                    return {'state': 'old_transition'}
                c.execute("INSERT OR IGNORE INTO streams(id,channel_id,title,game_name,tags_json,started_at,source,completeness,legacy_metadata_json,created_at,updated_at,revision) VALUES (?,?,?,?,'[]',?,'bot','partial','{}',?,?,1)", (key, channel, title, game, to_rfc3339(start), encoded, encoded))
                snapshot = StreamSnapshot(state='live', id=key, title=title, game=game, started_at=start, observed_at=at)
            else:
                rows = c.execute("SELECT id FROM streams WHERE channel_id=? AND source='bot' AND ended_at IS NULL AND started_at<=?", (channel, encoded)).fetchall()
                for row in rows:
                    c.execute('UPDATE streams SET ended_at=?,updated_at=?,revision=revision+1 WHERE id=?', (encoded, encoded, row['id']))
                    c.execute('UPDATE collection_runs SET stopped_at=MAX(started_at,?) WHERE stream_id=? AND stopped_at IS NULL', (encoded, row['id']))
                    c.execute("INSERT INTO stream_metric_state(stream_id,end_precision) VALUES (?,'estimated') ON CONFLICT(stream_id) DO UPDATE SET end_precision='estimated',revision=revision+1", (row['id'],))
                snapshot = StreamSnapshot(state='offline', observed_at=at)
            c.execute('INSERT OR IGNORE INTO stream_presence VALUES (?,?,?,?)', (channel, encoded, snapshot.state, snapshot.id))
        self.publish_transition(snapshot)
        return {'state': 'stream_transition'}

    def gap(self, reason='disconnected'):
        self.on_gap()
        with self.repository.transaction(write=True) as c:
            channel = self.repository.channel_id
            if not c.execute('SELECT 1 FROM eventsub_gaps WHERE channel_id=? AND ended_at IS NULL', (channel,)).fetchone():
                c.execute('INSERT INTO eventsub_gaps VALUES (?,?,?,NULL,?)',
                    (channel, uuid4().hex, to_rfc3339(self.repository.clock()), reason))

    def connected(self):
        with self.repository.transaction(write=True) as c:
            c.execute('UPDATE eventsub_gaps SET ended_at=? WHERE channel_id=? AND ended_at IS NULL',
                      (to_rfc3339(self.repository.clock()), self.repository.channel_id))

    def ingest(self, message):
        try:
            metadata, payload = message['metadata'], message['payload']
            subscription, event = payload['subscription'], payload['event']
            kind = subscription['type']
            if metadata['message_type'] != 'notification' or metadata['subscription_type'] != kind:
                raise ValueError
            channel = event.get('to_broadcaster_user_id') if kind == 'channel.raid' else event.get('broadcaster_user_id')
            if channel != self.repository.channel_id:
                raise ValueError
            key = metadata['message_id']
            if not isinstance(key, str) or not 1 <= len(key) <= 200:
                raise ValueError
            received = self.repository.clock()
            occurred = from_rfc3339(event['followed_at'] if kind == 'channel.follow' else metadata['message_timestamp'])
            if occurred > received:
                raise ValueError
            if kind in ('stream.online', 'stream.offline'):
                return self._stream_transition(kind, event, occurred)
            names = {'channel.follow': 'follow', 'channel.subscribe': 'subscribe',
                     'channel.subscription.message': 'resubscribe', 'channel.subscription.gift': 'gift_subscription',
                     'channel.cheer': 'cheer', 'channel.raid': 'raid',
                     'channel.channel_points_custom_reward_redemption.add': 'redemption',
                     'channel.prediction.end': 'prediction'}
            if kind not in names and kind != 'channel.chat.message':
                return {'state': 'ignored'}
            if kind == 'channel.subscribe' and event.get('is_gift') is True:
                return {'state': 'gift_covered_by_gift_event'}
            prefix = 'chatter_' if kind == 'channel.chat.message' else 'from_broadcaster_' if kind == 'channel.raid' else ''
            user_id = event.get(prefix+'user_id')
            person = Person(user_id, event.get(prefix+'user_login'), event.get(prefix+'user_name')) if user_id else None
            encoded = to_rfc3339(occurred)
            with self.repository.transaction() as c:
                if kind != 'channel.chat.message' and c.execute('SELECT 1 FROM channel_events WHERE channel_id=? AND id=?', (channel, key)).fetchone():
                    return {'state': 'duplicate'}
                presence = c.execute('SELECT * FROM stream_presence WHERE channel_id=? AND observed_at<=? ORDER BY observed_at DESC LIMIT 1', (channel, encoded)).fetchone()
                attribution, stream_id = 'unknown', None
                if presence and (occurred-from_rfc3339(presence['observed_at'])).total_seconds() <= 30:
                    attribution = 'stream' if presence['state'] == 'live' else 'offline'
                    stream_id = presence['stream_id']
                    if stream_id:
                        row = c.execute('SELECT started_at,ended_at FROM streams WHERE id=?', (stream_id,)).fetchone()
                        if row is None or encoded < row['started_at'] or (row['ended_at'] is not None and encoded >= row['ended_at']):
                            attribution, stream_id = 'unknown', None
            if kind == 'channel.chat.message':
                if person is None:
                    raise ValueError
                written = self.repository.record_chat(ChatMessage(event['message_id'], person, stream_id,
                    occurred, received, event['message']['text']))
                if written:
                    self.on_chat(event, stream_id, occurred)
                return {'state': 'recorded' if written else 'not_recorded', 'chat': True}
            amount = event.get('bits') if kind == 'channel.cheer' else event.get('total') if kind == 'channel.subscription.gift' else event.get('viewers') if kind == 'channel.raid' else None
            self.repository.record_event(ChannelEvent(key, names[kind], occurred, received, person,
                                                       stream_id, attribution, amount))
            return {'state': 'recorded'}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PersistenceError('invalid_eventsub_event', 'eventsub') from None
