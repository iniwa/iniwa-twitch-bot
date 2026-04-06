from flask import Blueprint, request, redirect, url_for
import config as c
from services.twitch_api import update_channel_info, send_chat

bp = Blueprint('presets', __name__)


@bp.route('/add_preset', methods=['POST'])
def add_preset():
    conf = c.load_config()
    raw_tags = request.form.get('tags', '')
    tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
    tweet_tags = request.form.get('tweet_tags', '').strip()

    conf['presets'].append({
        "name": request.form['name'],
        "game": request.form['game'],
        "title": request.form['title'],
        "tags": tags_list,
        "tweet_tags": tweet_tags
    })
    c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/delete_preset/<int:idx>', methods=['POST'])
def delete_preset(idx):
    conf = c.load_config()
    presets = conf.get('presets', [])
    if 0 <= idx < len(presets):
        presets.pop(idx)
        c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/update_preset', methods=['POST'])
def update_preset():
    idx = int(request.form['preset_index'])
    conf = c.load_config()
    presets = conf.get('presets', [])

    if 0 <= idx < len(presets):
        raw_tags = request.form.get('tags', '')
        tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
        tweet_tags = request.form.get('tweet_tags', '').strip()

        presets[idx] = {
            "name": request.form['name'],
            "game": request.form['game'],
            "title": request.form['title'],
            "tags": tags_list,
            "tweet_tags": tweet_tags
        }
        c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/apply_preset', methods=['POST'])
def apply_preset():
    idx = int(request.form['preset_index'])
    conf = c.load_config()
    presets = conf.get('presets', [])
    if 0 <= idx < len(presets):
        p = presets[idx]
        update_channel_info(conf, p['game'], p['title'], p.get('tags', []))
        conf['current_tweet_tags'] = p.get('tweet_tags', '')
        c.save_config(conf)
    return redirect(url_for('dashboard.index'))


@bp.route('/test_message', methods=['POST'])
def test_message():
    conf = c.load_config()
    send_chat(conf, "【Botテスト】これはテスト送信です！")
    c.log("Test Message Sent! ✅")
    c.state.reset()
    return redirect(url_for('dashboard.index'))
