"""Pure management/preview requests; chat dispatch belongs to its worker."""

from flask import Blueprint, current_app, jsonify, render_template, request
from ..application.persistence import PersistenceError
from .community import require_local_json, error_status

automation = Blueprint('v2_automation', __name__, template_folder='templates')


def _query():
    def one(name, default=None):
        values = request.args.getlist(name)
        if len(values) > 1:
            raise PersistenceError('invalid_query', 'automation')
        return values[0] if values else default
    sort, order, enabled = one('sort', 'name'), one('order', 'asc'), one('enabled')
    if sort not in ('name', 'updated_at') or order not in ('asc', 'desc'):
        raise PersistenceError('invalid_sort', 'automation')
    if enabled not in (None, 'enabled', 'disabled'):
        raise PersistenceError('invalid_filter', 'automation')
    return dict(sort=sort, order=order, enabled=None if enabled is None else enabled == 'enabled')


@automation.get('/v2/automation')
def automation_page():
    return render_template('v2/automation.html')


@automation.get('/api/v2/automation')
@automation.post('/api/v2/automation/policy')
@automation.post('/api/v2/automation/definition')
@automation.post('/api/v2/automation/preview')
def automation_api():
    try:
        body = require_local_json() if request.method == 'POST' else None
        service = current_app.extensions['twitchbot.container'].automation
        if service is None:
            raise PersistenceError('automation_unavailable', 'automation')
        if body is None:
            data = service.snapshot(**_query())
        elif request.path.endswith('/policy'):
            data = service.repository.save_policy(body.get('commands_enabled'), body.get('posts_enabled'), body.get('ignored'), body.get('revision'))
        elif request.path.endswith('/definition'):
            data = service.repository.save_definition(body.get('id'), body.get('kind'), body.get('name'), body.get('enabled'), body.get('specification'), body.get('revision'), body.get('position', 0))
        else:
            data = service.preview(body.get('specification'), body.get('input'), body.get('role'))
        response = jsonify(data)
    except PersistenceError as exc:
        response = current_app.make_response((jsonify(error=exc.code), error_status(exc.code)))
    response.headers['Cache-Control'] = 'no-store'
    return response
