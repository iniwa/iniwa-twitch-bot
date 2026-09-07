"""Synthetic, disposable WSGI fixture for device/browser checks.

Launch explicitly with gunicorn 'tools.v2_device_fixture:create_app()'. Use a
network-isolated container and a Unix socket forwarded over SSH. No production
configuration, Twitch credentials, scheduler or real adapters are loaded.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from twitchbot.adapters.persistence import SQLiteDatabase, StreamRepository, ChannelReadModelRepository
from twitchbot.adapters.persistence.analytics import AnalyticsRepository, HistoryReader
from twitchbot.adapters.persistence.community import CommunityRepository
from twitchbot.adapters.persistence.control import ControlRepository
from twitchbot.application.analytics import CollectionRun, ViewerObservation
from twitchbot.application.community import Person, ChannelEvent, ChatMessage
from twitchbot.application.control import ChannelPreset, ActionResult
from twitchbot.application.live import LiveSnapshot, StreamSnapshot
from twitchbot.application.live_actions import LiveActions
from twitchbot.application.persistence import StreamRecord, ChannelReadModel
from twitchbot.container import Container
from twitchbot.web.app import create_app as create_v2_app


def create_app():
    temporary = TemporaryDirectory(prefix='v2-browser-fixture-')
    db = SQLiteDatabase(Path(temporary.name)/'synthetic.sqlite3')
    db.migrate()
    now = datetime.now(timezone.utc)
    base = now-timedelta(minutes=20)
    StreamRepository(db).put(StreamRecord('s1', 'fixture', '検証用 — 架空データ',
        None, 'サンプルゲーム', None, (), base, None, None, 'bot', 'partial',
        None, None, None, None, {}, None), 0)
    analytics = AnalyticsRepository(db)
    analytics.start_run('s1', CollectionRun('run', base))
    for t in range(0, 1200, 20):
        if not 280 <= t < 440:
            analytics.append('s1', ViewerObservation('run', base+timedelta(seconds=t), 12+t//100+t % 13))
    community = CommunityRepository(db, 'fixture')
    for i, name in enumerate(['あおい', 'みずき', 'ひなた', 'かえで']):
        person = Person('u'+str(i), 'viewer_'+str(i), name)
        at = base+timedelta(seconds=80+i*180)
        community.record_event(ChannelEvent('e'+str(i), 'follow', at, now, person, 's1', 'stream'))
        community.record_chat(ChatMessage('m'+str(i), person, 's1', at, now, '新しいマップ、いいですね！'))
    snapshot = LiveSnapshot(stream=StreamSnapshot(state='live', id='s1',
        title='検証用 — 架空データ', game='サンプルゲーム', viewer_count=34,
        started_at=base, observed_at=now), generated_at=now, bot_enabled=True,
        bot_state='running', connections={'twitch': 'healthy'})

    class SyntheticLive:
        def snapshot(self):
            current = datetime.now(timezone.utc)
            return replace(snapshot, generated_at=current,
                           stream=replace(snapshot.stream, observed_at=current))

    class FakeTwitch:
        available = True

        def __init__(self):
            self.calls = []

        def create_marker(self, channel, description):
            self.calls.append('marker')
            return ActionResult('succeeded', 'fake-marker', 1200)

        def apply_preset(self, channel, preset):
            self.calls.append('preset')
            return ActionResult('succeeded')

    controls = ControlRepository(db, 'fixture')
    controls.save_preset(ChannelPreset('p1', 'いつものゲーム', '今日は続きを進める',
        'game', 'サンプルゲーム', ('日本語',)), 0)
    ChannelReadModelRepository(db).put(ChannelReadModel('fixture', '現在のタイトル',
        'game', 'サンプルゲーム', ('日本語',), None, now, 'fake'), 0)
    live, fake = SyntheticLive(), FakeTwitch()
    actions = LiveActions(controls, live, adapter=fake, runtime_allowed=lambda: True)
    app = create_v2_app(Container(live_provider=live, history_reader=HistoryReader(db),
                                 community=community, live_actions=actions))
    app.extensions['device_qa.temporary'] = temporary

    @app.get('/qa/state')
    def state():
        return {'synthetic': True, 'calls': fake.calls,
                'comments': community.person('u0')['items'][0]['comment_count']}

    @app.after_request
    def label(response):
        response.headers['X-Device-QA'] = 'synthetic'
        return response

    return app
