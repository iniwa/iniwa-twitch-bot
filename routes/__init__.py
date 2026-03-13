from routes import (
    dashboard, analytics, vod, rules,
    presets, predictions, settings, viewers
)


def register_blueprints(app):
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(analytics.bp)
    app.register_blueprint(vod.bp)
    app.register_blueprint(rules.bp)
    app.register_blueprint(presets.bp)
    app.register_blueprint(predictions.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(viewers.bp)
