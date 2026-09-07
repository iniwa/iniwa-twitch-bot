"""Flask factory for the side-effect-free v2 boundary."""

from __future__ import annotations

from typing import Any

from flask import Flask

from ..container import Container
from .health import health
from .live import live
from .history import history
from .community import community
from .control import control
from .operations import operations
from .automation import automation
from .predictions import predictions
from .login import login


def register_blueprints(application: Flask, container: Container | None = None) -> None:
    """Attach v2 routes to an explicitly owned host without starting workers."""
    if 'twitchbot.container' in application.extensions:
        raise ValueError('A Twitch Bot container is already registered')
    owned = container or Container()
    application.extensions["twitchbot.container"] = owned

    @application.context_processor
    def navigation_state():
        # Use only the detached in-memory snapshot. Navigation rendering must
        # never turn a page request into a Twitch request.
        state = owned.live_provider.snapshot().stream.state
        return {"nav_live": state in ("live", "degraded")}

    application.register_blueprint(health)
    application.register_blueprint(live)
    application.register_blueprint(history)
    application.register_blueprint(community)
    application.register_blueprint(control)
    application.register_blueprint(operations)
    application.register_blueprint(automation)
    application.register_blueprint(predictions)
    application.register_blueprint(login)


def create_app(container: Container | None = None, **flask_kwargs: Any) -> Flask:
    """Build an isolated app without loading legacy config or starting runtime."""
    application = Flask(__name__, **flask_kwargs)
    register_blueprints(application, container)
    return application
