from flask import Blueprint, redirect, url_for, jsonify, request
import threading
import config as c
from services.download import (
    execute_download, bulk_download_task,
    request_cancel_download, delete_vod_file, get_download_progress
)
from services.storage import load_stream_index, save_stream_index
from services.twitch_api import sync_vod_history

bp = Blueprint('vod', __name__)


@bp.route('/api/download_progress')
def api_download_progress():
    return jsonify(get_download_progress())


@bp.route('/download_vod/<stream_id>', methods=['POST'])
def download_vod_manual(stream_id):
    conf = c.load_config()
    threading.Thread(target=execute_download, args=(conf, stream_id)).start()
    return redirect(url_for('analytics.analytics_list'))


@bp.route('/download_all_vods', methods=['POST'])
def download_all_vods():
    conf = c.load_config()
    threading.Thread(target=bulk_download_task, args=(conf,)).start()
    return redirect(url_for('analytics.analytics_list'))


@bp.route('/cancel_download/<stream_id>', methods=['POST'])
def cancel_download(stream_id):
    request_cancel_download(stream_id)
    return redirect(url_for('analytics.analytics_list'))


@bp.route('/delete_vod/<stream_id>', methods=['POST'])
def delete_vod(stream_id):
    delete_vod_file(stream_id)
    return redirect(url_for('analytics.analytics_list'))


@bp.route('/update_stream_info', methods=['POST'])
def update_stream_info():
    stream_id = request.form.get('stream_id')
    new_title = request.form.get('title')
    new_game = request.form.get('game')

    if stream_id:
        idx = load_stream_index()
        if stream_id in idx:
            if new_title:
                idx[stream_id]['title'] = new_title
            if new_game:
                idx[stream_id]['game_name'] = new_game
            save_stream_index(idx)
            c.log(f"✏️ 配信情報を手動更新しました: {stream_id}")

    return redirect(url_for('analytics.analytics_list'))


@bp.route('/force_sync_history', methods=['POST'])
def force_sync_history():
    conf = c.load_config()
    threading.Thread(target=sync_vod_history, args=(conf, True), daemon=True).start()
    return redirect(url_for('analytics.analytics_list'))
