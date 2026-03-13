from flask import Blueprint, request, redirect, url_for, render_template
import config as c
from services.workers import get_debug_status
from services.storage import delete_history_data
from services.twitch_api import force_update_followers

bp = Blueprint('settings', __name__)


@bp.route('/toggle', methods=['POST'])
def toggle():
    conf = c.load_config()
    conf['is_running'] = not conf['is_running']
    c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/save_config', methods=['POST'])
def save_config_route():
    conf = c.load_config()

    for k in ['client_id', 'access_token', 'broadcaster_id', 'bot_user_id', 'channel_name']:
        conf[k] = request.form.get(k)
    conf['broadcaster_token'] = request.form.get('broadcaster_token')
    conf['debug_mode'] = 'debug_mode' in request.form
    conf['ignore_stream_status'] = 'ignore_stream_status' in request.form
    conf['enable_welcome'] = 'enable_welcome' in request.form
    conf['hide_self_bot'] = 'hide_self_bot' in request.form
    conf['enable_vod_download'] = 'enable_vod_download' in request.form
    conf.pop('vod_download_path', None)
    conf['ignored_users'] = [
        x.strip().lower()
        for x in request.form.get('ignored_users_list', '').replace(',', '\n').replace('\r', '').split('\n')
        if x.strip()
    ]

    if 'layout' not in conf:
        conf['layout'] = {"cards": {}}
    try:
        conf['layout']['columns'] = int(request.form.get('layout_columns', 2))
        conf['layout']['max_width'] = int(request.form.get('layout_max_width', 1400))

        card_keys = ['viewers', 'presets', 'prediction', 'rules', 'logs']
        for key in card_keys:
            if key not in conf['layout']['cards']:
                conf['layout']['cards'][key] = {}
            conf['layout']['cards'][key]['span'] = int(request.form.get(f'layout_{key}_span', 1))
            h_val = request.form.get(f'layout_{key}_height', 0)
            conf['layout']['cards'][key]['height'] = int(h_val) if h_val else 0
            order_val = request.form.get(f'layout_{key}_order', 0)
            conf['layout']['cards'][key]['order'] = int(order_val) if order_val else 0
    except (ValueError, TypeError) as e:
        c.log(f"⚠️ レイアウト設定の保存に失敗: {e}")

    c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/debug_check')
def debug_check():
    status = get_debug_status()
    return render_template('debug.html', status=status)


@bp.route('/debug_update_followers', methods=['POST'])
def debug_update_followers():
    conf = c.load_config()
    msg = force_update_followers(conf)
    c.log(f"Debug Result: {msg}")
    return redirect(url_for('dashboard.index'))


@bp.route('/delete_debug_history', methods=['POST'])
def delete_debug_history():
    delete_history_data('debug_stream')
    return redirect(url_for('dashboard.index'))
