"""New monitor and explicit local controls beside the protected v2 pilot."""

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request

from ..application.control import ChannelPreset
from ..application.persistence import PersistenceError
from .community import require_local_json, error_status, _paging

control = Blueprint("v2_control", __name__, template_folder="templates")


def _actions():
    value = current_app.extensions["twitchbot.container"].live_actions
    if value is None:
        raise PersistenceError("control_unavailable", "control")
    return value


def _json(result, status=200):
    response = current_app.make_response((jsonify(result), status))
    response.headers["Cache-Control"] = "no-store"
    return response


def _preset_sorting():
    values = {name: request.args.getlist(name) for name in ("sort", "order")}
    if any(len(items) > 1 for items in values.values()):
        raise PersistenceError("invalid_query", "control")
    sort = values["sort"][0] if values["sort"] else "name"
    return {"sort": sort, "order": values["order"][0] if values["order"] else ("asc" if sort == "name" else "desc")}


def _monitor():
    container = current_app.extensions["twitchbot.container"]
    snapshot = container.live_provider.snapshot().as_dict()
    stream_id = snapshot["stream"]["id"] if snapshot["stream"]["state"] in ("live","degraded") else None
    result = {"snapshot":snapshot,"events":None,"people":None,"notes":None,"analytics":None,"errors":{},
              "local_controls":container.live_actions is not None,
              "twitch_controls":bool(container.live_actions and container.live_actions.adapter.available)}
    queries = []
    if container.community:
        queries.append(("events",lambda:container.community.events(stream_id=stream_id,limit=20)))
        if stream_id:
            queries.append(("people",lambda:container.community.people(stream_id=stream_id,limit=20)))
    if stream_id and container.live_actions:
        queries.append(("notes",lambda:container.live_actions.repository.notes(stream_id,limit=20)))
    if stream_id and container.history_reader:
        queries.append(("analytics",lambda:container.history_reader.detail(stream_id)))
    if container.automation:
        def automation_status():
            status = container.automation.snapshot()
            return {'policy': status['policy'], 'state': status['state']}
        queries.append(('automation', automation_status))
    if container.predictions:
        def prediction_status():
            status = container.predictions.snapshot()
            return {'fresh': status['fresh'], 'items': [p for p in status['items'] if p['status'] in ('ACTIVE', 'LOCKED')]}
        queries.append(('predictions', prediction_status))
    for name, query in queries:
        try:
            result[name] = query()
        except PersistenceError as exc:
            result["errors"][name] = exc.code
    # Browser and recorder clocks can differ; age must use the recorder's clock.
    result["server_time"] = datetime.now(timezone.utc).isoformat()
    return result


@control.get("/api/v2/control")
def control_api():
    return _json(_monitor())


@control.get("/v2/control")
def control_page():
    response = current_app.make_response(render_template("v2/control.html",data=_monitor()))
    response.headers["Cache-Control"] = "no-store"
    return response


@control.get("/v2/presets")
def presets_page():
    data,error,status=None,None,200
    try:
        data=_actions().repository.presets(**_preset_sorting())
    except PersistenceError as exc:
        error,status=exc.code,error_status(exc.code)
    response=current_app.make_response((render_template("v2/presets.html",data=data,error=error),status))
    response.headers["Cache-Control"]="no-store"
    return response


@control.get("/api/v2/presets")
@control.get("/api/v2/streams/<stream_id>/notes")
@control.get("/api/v2/people/<user_id>/note")
@control.get("/api/v2/control-operations/<operation_id>")
def query(stream_id=None,user_id=None,operation_id=None):
    try:
        repo=_actions().repository
        if stream_id is not None:
            result=repo.notes(stream_id,**_paging())
        elif user_id is not None:
            result=repo.person_note(user_id)
        elif operation_id is not None:
            result=repo.operation(operation_id)
        else:
            result=repo.presets(**_preset_sorting())
        return _json(result)
    except PersistenceError as exc:
        return _json({"error":exc.code},error_status(exc.code))


@control.post("/api/v2/presets")
@control.post("/api/v2/preset-previews")
@control.post("/api/v2/preset-applications")
@control.post("/api/v2/streams/<stream_id>/markers")
@control.post("/api/v2/people/<user_id>/note")
def mutate(stream_id=None,user_id=None):
    try:
        body=require_local_json()
        actions=_actions()
        if stream_id is not None:
            result=actions.note(stream_id,body.get("body"),body.get("request_id"),marker=body.get("marker",False))
        elif user_id is not None:
            result=actions.repository.save_person_note(user_id,body.get("body"),body.get("revision"))
        elif request.path.endswith("/preset-previews"):
            result=actions.repository.preview_preset(body.get("preset_id"))
        elif request.path.endswith("/preset-applications"):
            result=actions.apply_preset(body.get("preview_id"),body.get("request_id"))
        else:
            tags,social=body.get("tags",[]),body.get("social_tags",[])
            if not isinstance(tags,list) or not isinstance(social,list):
                raise PersistenceError("invalid_tags","control")
            preset=ChannelPreset(body.get("id"),body.get("name"),body.get("title"),body.get("game_id"),body.get("game_name"),tuple(tags),tuple(social))
            result=actions.repository.save_preset(preset,body.get("revision"))
        return _json(result)
    except PersistenceError as exc:
        return _json({"error":exc.code},error_status(exc.code))
