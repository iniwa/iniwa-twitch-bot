"""Read-only v2 history boundary. No implicit database or Twitch access."""

import json

from flask import Blueprint, current_app, jsonify, render_template, request

from ..application.persistence import PersistenceError
from ..adapters.persistence.sqlite import from_rfc3339

history = Blueprint("v2_history", __name__, template_folder="templates")


def _reader():
    reader = current_app.extensions["twitchbot.container"].history_reader
    if reader is None:
        raise PersistenceError("history_unavailable", "history")
    return reader


def _query(kind, stream_id=None):
    reader = _reader()
    if kind == "detail":
        start = from_rfc3339(request.args["start"]) if "start" in request.args else None
        end = from_rfc3339(request.args["end"]) if "end" in request.args else None
        try:
            point_budget = int(request.args.get("points", "1200"))
        except ValueError:
            raise PersistenceError("invalid_point_budget", "history") from None
        return reader.detail(stream_id, start=start, end=end, point_budget=point_budget)
    if kind == "compare":
        return reader.compare(request.args.getlist("id"), scope=request.args.get("scope", "full"))
    try:
        for name in ('limit', 'cursor', 'sort', 'order'):
            if len(request.args.getlist(name)) > 1:
                raise ValueError
        limit = int(request.args.get("limit", "50"))
        if len(request.args.get("cursor", "")) > 1024:
            raise ValueError
        cursor = json.loads(request.args["cursor"]) if "cursor" in request.args else None
    except (ValueError, TypeError):
        raise PersistenceError("invalid_query", "history") from None
    result = reader.list_streams(limit=limit, before=cursor, sort=request.args.get('sort', 'started_at'), order=request.args.get('order', 'desc'))
    result["next_cursor_token"] = json.dumps(result["next_cursor"], separators=(",", ":")) if result["next_cursor"] else None
    return result


def _respond(kind, stream_id=None, *, page=False):
    data, error, status = None, None, 200
    try:
        data = _query(kind, stream_id)
    except PersistenceError as exc:
        error = exc.code
        status = 404 if error == "stream_not_found" else 409 if error == 'list_changed' else 503 if error in ("history_unavailable", "history_limit_exceeded") else 400
    if page:
        response = current_app.make_response((render_template("v2/history.html", kind=kind, data=data, error=error), status))
    else:
        response = current_app.make_response((jsonify(data if error is None else {"error": error}), status))
    response.headers["Cache-Control"] = "no-store"
    return response


@history.get("/api/v2/streams")
def streams_api():
    return _respond("list")


@history.get("/api/v2/streams/<stream_id>/analytics")
def analytics_api(stream_id):
    return _respond("detail", stream_id)


@history.get("/api/v2/stream-comparisons")
def comparisons_api():
    return _respond("compare")


@history.get("/v2/history")
def history_page():
    return _respond("list", page=True)


@history.get("/v2/history/<stream_id>")
def detail_page(stream_id):
    return _respond("detail", stream_id, page=True)


@history.get("/v2/history/compare")
def compare_page():
    return _respond("compare", page=True)
