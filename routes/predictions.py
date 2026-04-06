from flask import Blueprint, request, redirect, url_for
import config as c
from services.predictions import (
    create_prediction, resolve_prediction, cancel_prediction
)

bp = Blueprint('predictions', __name__)


@bp.route('/add_prediction_preset', methods=['POST'])
def add_prediction_preset():
    conf = c.load_config()
    if 'prediction_presets' not in conf:
        conf['prediction_presets'] = []

    raw_outcomes = request.form.getlist('outcomes')
    outcomes = [o.strip() for o in raw_outcomes if o.strip()]

    if len(outcomes) < 2:
        return redirect(url_for('dashboard.index'))

    try:
        conf['prediction_presets'].append({
            'title': request.form.get('title', ''),
            'outcomes': outcomes,
            'duration': max(1, int(request.form.get('duration', 120)))
        })
    except (ValueError, TypeError):
        return redirect(url_for('dashboard.index'))
    c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/update_prediction_preset', methods=['POST'])
def update_prediction_preset():
    conf = c.load_config()
    try:
        idx = int(request.form['preset_index'])
    except (ValueError, KeyError):
        return redirect(url_for('dashboard.index'))

    raw_outcomes = request.form.getlist('outcomes')
    outcomes = [o.strip() for o in raw_outcomes if o.strip()]

    presets = conf.get('prediction_presets', [])
    if 0 <= idx < len(presets) and len(outcomes) >= 2:
        presets[idx] = {
            'title': request.form.get('title', ''),
            'outcomes': outcomes,
            'duration': max(1, int(request.form.get('duration', 120)))
        }
        c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/delete_prediction_preset/<int:idx>', methods=['POST'])
def delete_prediction_preset(idx):
    conf = c.load_config()
    presets = conf.get('prediction_presets', [])
    if 0 <= idx < len(presets):
        presets.pop(idx)
        c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/start_prediction', methods=['POST'])
def start_prediction():
    conf = c.load_config()
    try:
        idx = int(request.form['preset_index'])
    except (ValueError, KeyError):
        return redirect(url_for('dashboard.index'))

    presets = conf.get('prediction_presets', [])
    if 0 <= idx < len(presets):
        preset = presets[idx]
        create_prediction(conf, preset['title'], preset['outcomes'], preset['duration'])
    return redirect(url_for('dashboard.index'))


@bp.route('/resolve_prediction', methods=['POST'])
def resolve_prediction_route():
    conf = c.load_config()
    pid = request.form.get('prediction_id')
    oid = request.form.get('winning_outcome_id')

    if not pid:
        return redirect(url_for('dashboard.index'))

    if oid:
        resolve_prediction(conf, pid, oid)
    else:
        cancel_prediction(conf, pid)

    return redirect(url_for('dashboard.index'))
