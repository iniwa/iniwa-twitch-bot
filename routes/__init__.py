from routes import (
    dashboard, analytics, vod, rules,
    presets, predictions, settings, viewers
)


def register_blueprints(app, *, v2_container=None):
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(analytics.bp)
    app.register_blueprint(vod.bp)
    app.register_blueprint(rules.bp)
    app.register_blueprint(presets.bp)
    app.register_blueprint(predictions.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(viewers.bp)

    if v2_container is not None:
        # Explicit candidate composition; the caller owns its worker lifecycle.
        from src.twitchbot.web.app import register_blueprints as register_v2
        register_v2(app, v2_container)
        return

    # The production app remains the sole Flask runtime.  Import through the
    # repository-root-resolvable namespace; Docker does not add /app/src.
    from src.twitchbot.container import Container
    from src.twitchbot.web.live import live as v2_live
    from routes.v2_pilot import LegacyCurrentStreamLiveProvider

    app.extensions['twitchbot.container'] = Container(
        live_provider=LegacyCurrentStreamLiveProvider(),
    )
    app.register_blueprint(v2_live)
