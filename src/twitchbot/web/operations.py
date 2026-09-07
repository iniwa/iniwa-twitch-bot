"""Cached operational status and durable manual backup requests."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
import json

from flask import Blueprint, current_app, jsonify, render_template, request

from ..application.persistence import PersistenceError
from .community import require_local_json, error_status

operations = Blueprint('v2_operations', __name__, template_folder='templates')

_BACKUP_STATES = ('local_ready', 'transfer_failed', 'nas_verified', 'retiring', 'expired')


def _service():
    value = current_app.extensions['twitchbot.container'].operations
    if value is None:
        raise PersistenceError('operations_unavailable', 'operations')
    return value


def _arg(name, default=None):
    values = request.args.getlist(name)
    if len(values) > 1:
        raise PersistenceError('invalid_query', 'backup')
    return values[0] if values else default


def _backup_query():
    sort = _arg('sort', 'created_at')
    order = _arg('order', 'desc')
    state = _arg('state') or None
    if sort not in ('created_at', 'size_bytes') or order not in ('asc', 'desc'):
        raise PersistenceError('invalid_sort', 'backup')
    if state is not None and state not in _BACKUP_STATES:
        raise PersistenceError('invalid_filter', 'backup')
    try:
        limit = int(_arg('limit', '50'))
    except (TypeError, ValueError):
        raise PersistenceError('invalid_limit', 'backup') from None
    if not 1 <= limit <= 200:
        raise PersistenceError('invalid_limit', 'backup')
    return sort, order, state, limit, _arg('cursor')


def _cursor(value):
    return urlsafe_b64encode(json.dumps(value, separators=(',', ':'), sort_keys=True).encode()).decode().rstrip('=')


def _decode_cursor(token, *, sort, order, state):
    if token is None:
        return None
    if not isinstance(token, str) or len(token) > 1024:
        raise PersistenceError('invalid_cursor', 'backup')
    try:
        raw = urlsafe_b64decode(token + '=' * (-len(token) % 4))
        value = json.loads(raw.decode())
    except (ValueError, TypeError, UnicodeDecodeError):
        raise PersistenceError('invalid_cursor', 'backup') from None
    expected = {'v': 1, 'sort': sort, 'order': order, 'state': state}
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items()):
        raise PersistenceError('invalid_cursor', 'backup')
    primary = value.get('value')
    if sort == 'created_at' and not isinstance(primary, str):
        raise PersistenceError('invalid_cursor', 'backup')
    if sort == 'size_bytes' and type(primary) is not int:
        raise PersistenceError('invalid_cursor', 'backup')
    if not isinstance(value.get('id'), str):
        raise PersistenceError('invalid_cursor', 'backup')
    return primary, value['id']


@operations.get('/v2/settings')
def settings_page():
    response = current_app.make_response(render_template('v2/settings.html'))
    response.headers['Cache-Control'] = 'no-store'
    return response


@operations.get('/api/v2/backups')
def backup_list_api():
    try:
        sort, order, state, limit, cursor = _backup_query()
        items = _service().backup_worker.service.list_backups(sort=sort, order=order, state=state)
        after = _decode_cursor(cursor, sort=sort, order=order, state=state)
        if after is not None:
            def key(item):
                return item[sort], item['id']
            items = [item for item in items if key(item) > after] if order == 'asc' else [item for item in items if key(item) < after]
        page = items[:limit]
        next_cursor = None
        if len(items) > limit:
            last = page[-1]
            next_cursor = _cursor({'v': 1, 'sort': sort, 'order': order, 'state': state,
                                   'value': last[sort], 'id': last['id']})
        response = jsonify({'items': page, 'next_cursor': next_cursor, 'sort': sort,
                            'order': order, 'state': state, 'limit': limit})
    except PersistenceError as exc:
        response = current_app.make_response((jsonify({'error': exc.code}), error_status(exc.code)))
    response.headers['Cache-Control'] = 'no-store'
    return response


@operations.get('/api/v2/operations')
@operations.post('/api/v2/recording-setting')
@operations.post('/api/v2/backup-policy')
@operations.post('/api/v2/backups')
@operations.post('/api/v2/restore-candidates')
def operation_api():
    try:
        body = require_local_json() if request.method == 'POST' else None
        service = _service()
        status = 200
        if body is None:
            data = service.snapshot()
        elif request.path.endswith('/recording-setting'):
            data = service.set_enabled(body.get('enabled'), body.get('revision'))
        elif request.path.endswith('/backup-policy'):
            data = service.update_backup_policy(body.get('enabled'), body.get('daily_hour'), body.get('revision'))
        elif request.path.endswith('/restore-candidates'):
            data, status = service.request_restore(body.get('request_id'), body.get('backup_id')), 202
        else:
            data, status = service.request_backup(body.get('request_id')), 202
        response = current_app.make_response((jsonify(data), status))
    except PersistenceError as exc:
        response = current_app.make_response((jsonify({'error': exc.code}), error_status(exc.code)))
    response.headers['Cache-Control'] = 'no-store'
    return response
