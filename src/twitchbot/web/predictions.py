"""Prediction management and preview-confirm requests; GET remains cached."""

from flask import Blueprint, current_app, jsonify, render_template, request
from ..application.persistence import PersistenceError
from .community import require_local_json, error_status

predictions = Blueprint('v2_predictions', __name__, template_folder='templates')


def _query():
    values = {name: request.args.getlist(name) for name in ('sort', 'order')}
    if any(len(items) > 1 for items in values.values()):
        raise PersistenceError('invalid_query', 'prediction')
    sort = values['sort'][0] if values['sort'] else 'name'
    order = values['order'][0] if values['order'] else 'asc'
    if sort not in ('name', 'updated_at') or order not in ('asc', 'desc'):
        raise PersistenceError('invalid_sort', 'prediction')
    return sort, order


@predictions.get('/v2/predictions')
def predictions_page():
    return render_template('v2/predictions.html')


@predictions.get('/api/v2/predictions')
@predictions.post('/api/v2/predictions/policy')
@predictions.post('/api/v2/predictions/preset')
@predictions.post('/api/v2/predictions/preview')
@predictions.post('/api/v2/predictions/confirm')
@predictions.post('/api/v2/predictions/refresh')
def predictions_api():
    try:
        body = require_local_json() if request.method == 'POST' else None
        container = current_app.extensions['twitchbot.container']
        service = container.predictions
        if service is None:
            raise PersistenceError('predictions_unavailable', 'prediction')
        status = 200
        if body is None:
            sort, order = _query()
            data = service.snapshot(sort=sort, order=order)
        elif request.path.endswith('/policy'):
            data = service.save_policy(body.get('enabled'), body.get('revision'))
        elif request.path.endswith('/preset'):
            data = service.save_preset(body.get('id'), body.get('name'), body.get('specification'), body.get('revision'))
        elif request.path.endswith('/preview'):
            data = service.preview(body.get('action'), body.get('target'), body.get('winning_outcome_id'))
        else:
            data = service.confirm(body.get('id')) if request.path.endswith('/confirm') else {'state': 'refresh_requested'}
            for worker in getattr(container.runtime, 'workers', ()):
                if worker.name == 'predictions': worker.wake()
            status = 202
        response = current_app.make_response((jsonify(data), status))
    except PersistenceError as exc:
        response = current_app.make_response((jsonify(error=exc.code), error_status(exc.code)))
    response.headers['Cache-Control'] = 'no-store'
    return response
