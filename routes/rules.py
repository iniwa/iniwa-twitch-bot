from flask import Blueprint, request, redirect, url_for
import config as c

bp = Blueprint('rules', __name__)


@bp.route('/save_rules', methods=['POST'])
def save_rules():
    conf = c.load_config()
    if 'game' in request.form:
        conf['rules'] = [
            {
                "name": n, "game": g, "message": m,
                "interval": int(i), "min_comments": int(mn)
            }
            for n, g, m, i, mn in zip(
                request.form.getlist('name'),
                request.form.getlist('game'),
                request.form.getlist('message'),
                request.form.getlist('interval'),
                request.form.getlist('min_comments')
            )
        ]
    c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/move_rule/<int:idx>/<direction>', methods=['POST'])
def move_rule(idx, direction):
    conf = c.load_config()
    rules = conf.get('rules', [])
    if direction == 'up' and idx > 0:
        rules[idx], rules[idx - 1] = rules[idx - 1], rules[idx]
    elif direction == 'down' and idx < len(rules) - 1:
        rules[idx], rules[idx + 1] = rules[idx + 1], rules[idx]
    conf['rules'] = rules
    c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/add_rule', methods=['POST'])
def add_rule():
    conf = c.load_config()
    new_rule = {
        "name": request.form.get('name', f"ルール #{len(conf.get('rules', [])) + 1}"),
        "game": request.form.get('game', 'All'),
        "message": request.form.get('message', ''),
        "interval": int(request.form.get('interval', 15)),
        "min_comments": int(request.form.get('min_comments', 2))
    }
    if 'rules' not in conf:
        conf['rules'] = []
    conf['rules'].append(new_rule)
    c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/delete_rule/<int:idx>', methods=['POST'])
def delete_rule(idx):
    conf = c.load_config()
    rules = conf.get('rules', [])
    if 0 <= idx < len(rules):
        rules.pop(idx)
        c.save_config(conf)
    return redirect(url_for('dashboard.index'))
