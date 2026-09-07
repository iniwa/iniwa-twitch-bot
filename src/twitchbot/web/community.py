"""Local community queries and explicit, previewed chat-body deletion."""

import json

from flask import Blueprint, current_app, jsonify, render_template, request, url_for

from ..application.persistence import PersistenceError
from ..adapters.persistence.sqlite import from_rfc3339

community = Blueprint("v2_community", __name__, template_folder="templates")


def require_local_json():
    # No CORS grant: browser writes must originate from this application.
    if request.headers.get("Origin") != request.host_url.rstrip("/") or request.headers.get("Sec-Fetch-Site") == "cross-site":
        raise PersistenceError("origin_rejected", "web")
    if not request.is_json or request.content_length is None or request.content_length > 65_536:
        raise PersistenceError("invalid_request", "web")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise PersistenceError("invalid_request", "web")
    return body


def _repository():
    repository = current_app.extensions["twitchbot.container"].community
    if repository is None:
        raise PersistenceError("community_unavailable", "community")
    return repository


def _paging():
    try:
        token = _arg("cursor")
        if token is not None and len(token) > 1024:
            raise ValueError
        return {"limit": int(_arg("limit") or "50"), "before": json.loads(token) if token is not None else None}
    except (ValueError, TypeError):
        raise PersistenceError("invalid_query", "community") from None


def _sorting(kind):
    defaults = {"events": "sort_at", "followers": "detected_at",
                "follower_status": "evidence_at", "people": "last_seen_at",
                "person": "last_seen_at", "chats": "occurred_at"}
    sort = _arg("sort") or defaults[kind]
    return {"sort": sort,
            "order": _arg("order") or ("asc" if sort == "name" else "desc")}


def _arg(name):
    values = request.args.getlist(name)
    if len(values) > 1:
        raise PersistenceError("invalid_query", "community")
    return values[0] or None if values else None


def _runtime_sync_status():
    """Read the worker snapshot only; opening a page must never start a sync."""
    runtime = current_app.extensions["twitchbot.container"].runtime
    worker = next((item for item in getattr(runtime, "workers", ())
                   if getattr(item, "name", None) == "followers"), None)
    if worker is None:
        return "unknown"
    snapshot = worker.snapshot()
    if snapshot.get("state") == "stopped":
        return "paused"
    if snapshot.get("state") == "degraded":
        return "failed"
    result = snapshot.get("result") or {}
    state = result.get("state", "unknown")
    return state if state in {"collecting", "waiting", "paused", "authorization_required",
                              "failed", "superseded", "complete"} else "unknown"


def _query(kind, user_id=None):
    repo = _repository()
    options = {**_paging(), **_sorting(kind)}
    if kind == "events":
        return repo.events(stream_id=_arg("stream_id"), attribution=_arg("attribution"), **options)
    if kind == "followers":
        result = repo.followers(user_id=_arg("user_id"), kind=_arg("kind"), **options)
        result["sync_status"] = _runtime_sync_status()
        return result
    if kind == "follower_status":
        result = repo.follower_status(status=_arg("status"), **options)
        result["sync_status"] = _runtime_sync_status()
        return result
    if kind == "person":
        return repo.person(user_id, **options)
    if kind == "chats":
        return repo.chats(stream_id=_arg("stream_id"), **options)
    return repo.people(stream_id=_arg("stream_id"), follow_status=_arg("follow_status"), **options)


def error_status(code):
    if code == "origin_rejected":
        return 403
    if code.endswith("_not_found"):
        return 404
    if code.endswith("_unavailable"):
        return 503
    if code in ("preview_changed", "preview_expired", "record_conflict", "revision_conflict", "list_changed"):
        return 409
    return 400


def _respond(kind, user_id=None, *, page=False):
    data, error, status, next_url = None, None, 200, None
    try:
        data = _query(kind, user_id)
        if data["next_cursor"]:
            token = json.dumps(data["next_cursor"], separators=(",", ":"))
            data["next_cursor_token"] = token
            params = {**request.args.to_dict(), "cursor": token, **(request.view_args or {})}
            next_url = url_for(request.endpoint, **params)
    except PersistenceError as exc:
        error, status = exc.code, error_status(exc.code)
    def sort_url(key):
        params = request.args.to_dict()
        params.pop("cursor", None)
        current = data.get("sort") if data else _sorting(kind)["sort"]
        current_order = data.get("order") if data else _sorting(kind)["order"]
        params["sort"] = key
        params["order"] = ("desc" if current_order == "asc" else "asc") if current == key else ("asc" if key == "name" else "desc")
        return url_for(request.endpoint, **{**(request.view_args or {}), **params})

    response = current_app.make_response((render_template("v2/community_page.html", kind=kind, data=data, error=error, next_url=next_url, sort_url=sort_url) if page else jsonify(data if error is None else {"error": error}), status))
    response.headers["Cache-Control"] = "no-store"
    return response


@community.get("/api/v2/events")
def events_api():
    return _respond("events")


@community.get("/api/v2/follow-history")
def followers_api():
    return _respond("followers")


@community.get("/api/v2/follower-status")
def follower_status_api():
    return _respond("follower_status")


@community.get("/api/v2/viewers")
def viewers_api():
    return _respond("people")


@community.get("/api/v2/viewers/<user_id>/history")
def person_api(user_id):
    return _respond("person", user_id)


@community.get("/api/v2/chat-messages")
def chats_api():
    return _respond("chats")


@community.get("/v2/community")
def community_page():
    return _respond("people", page=True)


@community.get("/v2/community/events")
def events_page():
    return _respond("events", page=True)


@community.get("/v2/community/followers")
def followers_page():
    return _respond("followers", page=True)


@community.get("/v2/community/followers/current")
def follower_status_page():
    return _respond("follower_status", page=True)


@community.get("/v2/community/people/<user_id>")
def person_page(user_id):
    return _respond("person", user_id, page=True)


@community.get("/v2/community/chat")
def chats_page():
    return _respond("chats", page=True)


@community.post("/api/v2/chat-body-deletion-previews")
@community.post("/api/v2/chat-body-deletions")
def delete_bodies():
    try:
        body = require_local_json()
        repo = _repository()
        if request.path.endswith("-previews"):
            result = repo.preview_body_deletion(from_rfc3339(body.get("start")), from_rfc3339(body.get("end")))
        else:
            result = repo.delete_chat_bodies(body.get("preview_id"))
        response = jsonify(result)
    except PersistenceError as exc:
        response = current_app.make_response((jsonify({"error": exc.code}), error_status(exc.code)))
    response.headers["Cache-Control"] = "no-store"
    return response
