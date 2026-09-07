"""Compose old and new routes without reading configuration or starting workers."""

from pathlib import Path

from flask import Flask, redirect, render_template

from . import register_blueprints
from .filters import register_filters


def create_app(*, v2_container=None, primary=False, channel_name=''):
    root = Path(__file__).resolve().parent.parent
    app = Flask('app', root_path=str(root))
    register_filters(app)
    register_blueprints(app, v2_container=v2_container)
    if primary:
        if v2_container is None or v2_container.operations is None:
            raise ValueError('Operational v2 container required')
        def stream_status():
            stream = v2_container.live_provider.snapshot().stream
            if stream.id is None or stream.state not in ('live', 'degraded'):
                return None
            return {'id': stream.id, 'title': stream.title, 'game_name': stream.game,
                    'started_at': stream.started_at, 'channel_name': channel_name}
        app.extensions['twitchbot.stream_status'] = stream_status
        app.view_functions['dashboard.index'] = lambda: redirect('/v2/control')
        app.context_processor(lambda: {'legacy_archives_available': True})
        @app.get('/legacy/viewers')
        def legacy_viewers():
            from .dashboard import get_history_api_data
            return render_template('legacy_viewers.html', people=get_history_api_data())
    return app
