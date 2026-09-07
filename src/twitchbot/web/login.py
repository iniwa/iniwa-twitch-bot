"""Local login requests; all OAuth network requests belong to the worker."""

from flask import Blueprint, current_app, jsonify, render_template, request

from ..application.persistence import PersistenceError
from .community import require_local_json, error_status

login = Blueprint('v2_login', __name__, template_folder='templates')


@login.get('/v2/connect')
def connect_page():
    response = current_app.make_response(render_template('v2/connect.html'))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@login.get('/api/v2/login')
@login.post('/api/v2/login/start')
@login.post('/api/v2/login/cancel')
def login_api():
    try:
        body = require_local_json() if request.method == 'POST' else None
        service = current_app.extensions['twitchbot.container'].login
        if service is None:
            if body is not None: raise PersistenceError('oauth_not_configured', 'login')
            result = {'configured':False, 'ready':False, 'accounts':{}}
        elif body is None:
            result = service.snapshot()
        elif request.path.endswith('/start'):
            result = service.begin(body.get('role'), predictions=body.get('predictions', False))
        else:
            result = service.cancel(body.get('role'))
        response = current_app.make_response(jsonify(result))
    except PersistenceError as exc:
        response = current_app.make_response((jsonify({'error':exc.code}), error_status(exc.code)))
    response.headers['Cache-Control'] = 'no-store'
    return response
