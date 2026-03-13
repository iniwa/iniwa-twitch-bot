from flask import Blueprint, request, redirect, url_for
import config as c
from services.twitch_api import perform_shoutout

bp = Blueprint('viewers', __name__)


@bp.route('/save_memo', methods=['POST'])
def save_memo():
    uid = request.form.get('user_id')
    memo = request.form.get('memo')
    if uid:
        with c.file_lock:
            db = c.load_viewers()
            if uid in db:
                db[uid]['memo'] = memo
                c.save_viewers(db)
    return redirect(url_for('dashboard.index'))


@bp.route('/shoutout', methods=['POST'])
def shoutout_route():
    conf = c.load_config()
    target = request.form.get('target_name')
    if target:
        s, m = perform_shoutout(conf, target)
        c.log(m)
    return redirect(url_for('dashboard.index'))
