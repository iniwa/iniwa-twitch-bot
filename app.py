from flask import Flask, request, render_template_string, redirect, url_for, jsonify
from datetime import datetime, timedelta
import json
import os
import threading
import re
import config as c 
import workers as w
from templates import HTML_TEMPLATE, ANALYTICS_TEMPLATE

w.start_workers()
app = Flask(__name__)

# --- Routes ---
@app.template_filter('to_datetime')
def to_datetime(iso_str):
    if not iso_str: return None
    try:
        # UTC(Z)をJSTに変換
        return datetime.fromisoformat(iso_str.replace('Z', '+00:00')).astimezone(c.JST)
    except:
        return None

@app.template_filter('seconds')
def seconds_filter(val):
    try:
        return timedelta(seconds=int(val))
    except:
        return timedelta(seconds=0)

@app.template_filter('duration_format')
def duration_format(val):
    if not val: return "0m"
    if isinstance(val, timedelta):
        seconds = int(val.total_seconds())
    else:
        seconds = int(val)
    if seconds < 0: return "まもなく終了"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h > 0 else f"{m}m"

@app.template_filter('timestamp_to_date')
def timestamp_to_date(ts):
    if not ts: return "-"
    return datetime.fromtimestamp(ts, c.JST).strftime('%Y-%m-%d %H:%M')

@app.template_filter('format_date')
def format_date(iso_str):
    if not iso_str: return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00')).astimezone(c.JST)
        return dt.strftime('%Y/%m/%d %H:%M')
    except: return iso_str

@app.route('/api/download_progress')
def api_download_progress():
    return jsonify(w.get_download_progress())

def get_history_api_data():
    conf = c.load_config(); db = c.load_viewers()
    active_uids = set(c.current_session_viewers.keys())
    ignored_ids = [conf.get('broadcaster_id'), conf.get('bot_user_id')] if conf.get('hide_self_bot') else []
    ignored_logins = set(conf.get('ignored_users', []))
    data_list = []
    for uid, v in db.items():
        if uid in ignored_ids: continue
        if v.get('login', '').lower() in ignored_logins: continue
        follow_sort = v["followed_at"].replace("-", "") if (v.get("is_follower") and v.get("followed_at")) else 0
        dur_val = v.get("total_duration", 0); h, rem = divmod(dur_val, 3600); m, s = divmod(rem, 60); dur_str = f"{h}h {m}m" if h > 0 else f"{m}m"
        is_active = uid in active_uids; ls_val = v.get("last_seen_ts", 0); ls_sort = 9999999999 if is_active else ls_val; ls_str = '<span class="watching-status">視聴中</span>' if is_active else timestamp_to_date(ls_val)
        data_list.append({"uid": uid, "name": v.get("name", ""), "login": v.get("login", "") or "-", "is_follower": v.get("is_follower", False), "followed_at": v.get("followed_at", ""), "follow_sort": follow_sort, "total_visits": v.get("total_visits", 0), "total_duration": dur_str, "total_duration_raw": dur_val, "total_comments": v.get("total_comments", 0), "total_bits": v.get("total_bits", 0), "is_sub": v.get("is_sub", False), "streak": v.get("streak", 0), "memo": v.get("memo", ""), "memo_esc": v.get("memo", "").replace("'", "\\'"), "last_seen_sort": ls_sort, "last_seen_str": ls_str})
    return data_list

def get_active_viewers_data():
    conf = c.load_config(); db = c.load_viewers()
    now = c.get_now()
    active_viewers = []; ignored_ids = [conf.get('broadcaster_id'), conf.get('bot_user_id')] if conf.get('hide_self_bot') else []; ignored_logins = set(conf.get('ignored_users', []))
    for uid, info in c.current_session_viewers.items():
        if uid in ignored_ids: continue
        if info.get('login', '').lower() in ignored_logins: continue
        ud = db.get(uid, {"total_visits":1}); d = now - info['joined_at']; h, rem = divmod(d.seconds, 3600); m, s = divmod(rem, 60)
        follow_str = f'<span class="follow-status">✅</span> <span style="font-size:0.75em; color:#888;">{ud.get("followed_at", "")}</span>' if ud.get("is_follower") else "-"
        active_viewers.append({"uid": uid, "name": info['name'], "login": info.get('login', ''), "duration": f"{h}h {m}m" if h else f"{m}m", "total": ud.get('total_visits', 1), "streak": ud.get('streak', 0), "follow_status": follow_str, "memo": ud.get("memo", "")})
    return active_viewers

@app.route('/api/status')
def api_status(): return jsonify({"logs": c.logs, "viewers": get_active_viewers_data(), "count": len(c.current_session_viewers)})
@app.route('/api/history')
def api_history(): return jsonify(get_history_api_data())

@app.route('/')
def index():
    conf = c.load_config(); db = c.load_viewers(); active_viewers = get_active_viewers_data()
    ignored_ids = [conf.get('broadcaster_id'), conf.get('bot_user_id')] if conf.get('hide_self_bot') else []; ignored_logins = set(conf.get('ignored_users', []))
    active_uids = set(c.current_session_viewers.keys()); filtered_history = {k: v for k, v in db.items() if k not in ignored_ids and v.get('login', '').lower() not in ignored_logins}
    current_game = c.current_game if c.current_game else ""; unique_games = sorted(list(set([p['game'] for p in conf.get('presets', [])] + [r['game'] for r in conf.get('rules', [])])))
    current_total_comments = c.state.get_count()
    
    now = c.get_now()

    for i, rule in enumerate(conf.get("rules", [])):
        rule["is_active"] = False; rule["reason"] = "-"
        has_specific = any(r["game"] == current_game for r in conf.get("rules", []) if r["game"] not in ["All", "Default"]); g = rule["game"]
        if g == "All": rule["is_active"] = True
        elif g == "Default": rule["is_active"] = not has_specific; rule["reason"] = "専用優先" if has_specific else ""
        elif g == current_game: rule["is_active"] = True
        else: rule["reason"] = "不一致"
        if rule["is_active"]:
            last = c.rule_last_executed.get(i, datetime.min.replace(tzinfo=c.JST)); last_count = c.rule_last_comment_count.get(i, 0); diff = current_total_comments - last_count; remaining = int(rule.get("min_comments", 2)) - diff; rule["remaining_comments"] = max(0, remaining)
            if last == datetime.min.replace(tzinfo=c.JST): rule["next_run"] = "すぐ"
            else: mins = int(((last + timedelta(minutes=int(rule.get("interval", 15)))) - now).total_seconds() / 60); rule["next_run"] = "条件待ち" if mins < 0 else f"あと約{mins}分"
    
    current_prediction = w.get_current_prediction(conf)

    return render_template_string(HTML_TEMPLATE, config=conf, logs=c.logs, active_viewers=active_viewers, history=filtered_history, active_uids=active_uids, current_game=current_game, unique_games=unique_games, current_prediction=current_prediction, now=now)

@app.route('/analytics')
def analytics_list():
    index_file = 'data/history/stream_index.json'
    index_data = {}
    sorted_list = []
    
    if os.path.exists(index_file):
        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
                for sid, data in index_data.items():
                    data['sid'] = sid
                    
                    if data.get('start_time'):
                        try:
                            dt = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
                            dt_jst = dt.astimezone(c.JST)
                            data['start_time'] = dt_jst.isoformat()
                        except: pass

                    dur = data.get('duration', '')
                    if dur and 'm' in dur and 's' in dur:
                        data['duration_short'] = re.sub(r'\d+s$', '', dur)
                    else:
                        data['duration_short'] = dur
                    sorted_list.append(data)
                sorted_list.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        except: pass

    all_streams_json = json.dumps(sorted_list, ensure_ascii=False)
    return render_template_string(ANALYTICS_TEMPLATE, view='list', index_data=index_data, sorted_list=sorted_list, all_streams_json=all_streams_json)

@app.route('/analytics/stream/<stream_id>')
def analytics_detail(stream_id):
    index_file = 'data/history/stream_index.json'; stream_info = {"title": "不明", "start_time": None}
    if os.path.exists(index_file):
        try:
            with open(index_file, 'r', encoding='utf-8') as f: stream_info = json.load(f).get(stream_id, stream_info)
        except: pass
    log_file = f"data/history/stream_{stream_id}.jsonl"; has_log_file = False
    chart_labels, chart_viewers, chart_comments, chat_logs, total_emote_counts, unique_chatters = [], [], [], [], {}, set()
    total_comments = 0; max_viewers = 0; sum_viewers = 0; count_logs = 0; total_subs = 0; total_bits = 0; total_points = 0
    
    if os.path.exists(log_file):
        has_log_file = True
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        dt = datetime.fromisoformat(data.get("timestamp", ""))
                        time_label = dt.strftime('%H:%M')
                        v = data['metrics']['viewer_count']; c_spd = data['metrics']['msg_speed']; chart_labels.append(time_label); chart_viewers.append(v); chart_comments.append(c_spd)
                        max_viewers = max(max_viewers, v); sum_viewers += v; count_logs += 1; total_bits += data['metrics'].get('bits', 0)
                        subs = data.get('subs', {}); total_subs += sum(subs.values()) + data['metrics'].get('gift_subs', 0)
                        for msg in data.get("messages", []): chat_logs.append({"type": "msg", "time": time_label, "user": msg['user'], "text": msg['text'], "is_sub": msg['is_sub']}); unique_chatters.add(msg['user']); total_comments += 1
                        for pt in data.get("points", []): chat_logs.append({"type": "point", "time": time_label, "user": pt['user'], "reward_id": pt['reward_id'], "text": pt.get('text','')}); total_points += 1
                        for eid, cnt in data.get("emotes", {}).items(): total_emote_counts[eid] = total_emote_counts.get(eid, 0) + cnt
                    except: pass
        except: pass
        avg_viewers = round(sum_viewers / count_logs, 1) if count_logs > 0 else 0; top_emotes = sorted(total_emote_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        stats = {"max_viewers": max_viewers, "avg_viewers": avg_viewers, "total_comments": total_comments, "unique_chatters": len(unique_chatters), "total_subs": total_subs, "total_bits": total_bits, "total_points": total_points}
        return render_template_string(ANALYTICS_TEMPLATE, view='detail', stream_info=stream_info, has_log_file=True, stats=stats, chart_labels=chart_labels, chart_viewers=chart_viewers, chart_comments=chart_comments, chat_logs=chat_logs, top_emotes=top_emotes)
    else:
        return render_template_string(ANALYTICS_TEMPLATE, view='detail', stream_info=stream_info, has_log_file=False)

@app.route('/download_vod/<stream_id>', methods=['POST'])
def download_vod_manual(stream_id):
    conf = c.load_config()
    # idx = w.load_stream_index()  <-- 不要なので削除
    # argsから idx を削除
    threading.Thread(target=w.execute_download, args=(conf, stream_id)).start()
    return redirect(url_for('analytics_list'))

@app.route('/download_all_vods', methods=['POST'])
def download_all_vods():
    conf = c.load_config()
    threading.Thread(target=w.bulk_download_task, args=(conf,)).start()
    return redirect(url_for('analytics_list'))

@app.route('/cancel_download/<stream_id>', methods=['POST'])
def cancel_download(stream_id):
    w.request_cancel_download(stream_id)
    return redirect(url_for('analytics_list'))

@app.route('/delete_vod/<stream_id>', methods=['POST'])
def delete_vod(stream_id):
    w.delete_vod_file(stream_id)
    return redirect(url_for('analytics_list'))

@app.route('/debug_check')
def debug_check():
    status = w.get_debug_status()
    html = f"""<html><body style="font-family:monospace; padding:20px; background:#222; color:#0f0;">
        <h1>🏥 System Diagnosis</h1><hr>
        <h3>📡 IRC Connection</h3><p>Status: {'✅ CONNECTED' if status['irc_connected'] else '❌ DISCONNECTED'}</p>
        <p>Last Activity: {status['last_irc_msg']}</p><hr>
        <h3>📊 Current Buffer</h3><p>Messages: {status['buffered_messages']}</p><hr>
        <h3>📈 API Status</h3><p>Total Followers: {status['current_follower_count']}</p>
        <p>Log Directory: {'✅ YES' if status['log_path_exists'] else '❌ NO'}</p>
        <p>yt-dlp Installed: {'✅ YES' if status['yt_dlp_installed'] else '❌ NO'}</p>
        <p>Download Dir: {status['download_dir']}</p><hr>
        <button onclick="window.close()" style="padding:10px;">Close</button></body></html>"""
    return html

@app.route('/toggle', methods=['POST'])
def toggle(): conf = c.load_config(); conf['is_running'] = not conf['is_running']; c.save_config(conf); return redirect(url_for('index'))

@app.route('/save_memo', methods=['POST'])
def save_memo():
    uid = request.form.get('user_id'); memo = request.form.get('memo')
    if uid:
        with c.file_lock:
            db = c.load_viewers()
            if uid in db:
                db[uid]['memo'] = memo
                c.save_viewers(db)
    return redirect(url_for('index'))

@app.route('/shoutout', methods=['POST'])
def shoutout_route():
    conf = c.load_config(); target = request.form.get('target_name')
    if target: s, m = w.perform_shoutout(conf, target); c.log(m)
    return redirect(url_for('index'))

@app.route('/save_rules', methods=['POST'])
def save_rules():
    conf = c.load_config()
    if 'game' in request.form:
        conf['rules'] = [{"name":n,"game":g,"message":m,"interval":int(i),"min_comments":int(mn)} for n,g,m,i,mn in zip(request.form.getlist('name'), request.form.getlist('game'), request.form.getlist('message'), request.form.getlist('interval'), request.form.getlist('min_comments'))]
    c.save_config(conf); return redirect(url_for('index'))

@app.route('/move_rule/<int:idx>/<direction>', methods=['POST'])
def move_rule(idx, direction):
    conf = c.load_config(); rules = conf.get('rules', [])
    if direction == 'up' and idx > 0: rules[idx], rules[idx-1] = rules[idx-1], rules[idx]
    elif direction == 'down' and idx < len(rules) - 1: rules[idx], rules[idx+1] = rules[idx+1], rules[idx]
    conf['rules'] = rules; c.save_config(conf); return redirect(url_for('index'))

@app.route('/add_rule', methods=['POST'])
def add_rule(): conf = c.load_config(); conf['rules'].append({"name": f"ルール #{len(conf['rules'])+1}", "game":"All", "message":"", "interval":15, "min_comments":2}); c.save_config(conf); return redirect(url_for('index'))

@app.route('/delete_rule/<int:idx>', methods=['POST'])
def delete_rule(idx): conf = c.load_config(); conf['rules'].pop(idx); c.save_config(conf); return redirect(url_for('index'))

@app.route('/add_preset', methods=['POST'])
def add_preset():
    conf = c.load_config()
    # タグ(Twitch)
    raw_tags = request.form.get('tags', '')
    tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
    
    # ツイート用タグ (★追加)
    tweet_tags = request.form.get('tweet_tags', '').strip()
    
    conf['presets'].append({
        "name": request.form['name'],
        "game": request.form['game'],
        "title": request.form['title'],
        "tags": tags_list,
        "tweet_tags": tweet_tags # ★保存
    })
    c.save_config(conf)
    return redirect(url_for('index'))

@app.route('/delete_preset/<int:idx>', methods=['POST'])
def delete_preset(idx): conf = c.load_config(); conf['presets'].pop(idx); c.save_config(conf); return redirect(url_for('index'))

@app.route('/update_preset', methods=['POST'])
def update_preset():
    idx = int(request.form['preset_index'])
    conf = c.load_config()
    
    raw_tags = request.form.get('tags', '')
    tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]

    # ツイート用タグ (★追加)
    tweet_tags = request.form.get('tweet_tags', '').strip()

    conf['presets'][idx] = {
        "name": request.form['name'],
        "game": request.form['game'],
        "title": request.form['title'],
        "tags": tags_list,
        "tweet_tags": tweet_tags # ★更新
    }
    c.save_config(conf)
    return redirect(url_for('index'))

@app.route('/apply_preset', methods=['POST'])
def apply_preset():
    idx = int(request.form['preset_index'])
    conf = c.load_config()
    p = conf['presets'][idx]
    
    # Twitchの情報を更新
    w.update_channel_info(conf, p['game'], p['title'], p.get('tags', []))
    
    # ★現在適用中のツイートタグとしてconfigに保存
    conf['current_tweet_tags'] = p.get('tweet_tags', '')
    c.save_config(conf)
    
    return redirect(url_for('index'))

@app.route('/test_message', methods=['POST'])
def test_message():
    conf = c.load_config(); w.send_chat(conf, "【Botテスト】これはテスト送信です！"); c.log("Test Message Sent! ✅"); c.state.reset(); return redirect(url_for('index'))

@app.route('/debug_update_followers', methods=['POST'])
def debug_update_followers():
    conf = c.load_config(); msg = w.force_update_followers(conf); c.log(f"Debug Result: {msg}"); return redirect(url_for('index'))

@app.route('/update_stream_info', methods=['POST'])
def update_stream_info():
    stream_id = request.form.get('stream_id')
    new_title = request.form.get('title')
    new_game = request.form.get('game')
    
    if stream_id:
        idx = w.load_stream_index()
        if stream_id in idx:
            # 変更がある場合のみ更新
            if new_title: idx[stream_id]['title'] = new_title
            if new_game: idx[stream_id]['game_name'] = new_game
            w.save_stream_index(idx)
            c.log(f"✏️ 配信情報を手動更新しました: {stream_id}")
            
    return redirect(url_for('analytics_list'))

@app.route('/api/current_settings')
def api_current_settings():
    conf = c.load_config()
    info = w.get_channel_info_by_id(conf, conf['broadcaster_id'])
    
    # 現在のツイートタグを取得 (未設定なら空文字)
    current_tweet_tags = conf.get('current_tweet_tags', '')
    
    if info:
        return jsonify({
            "status": "success",
            "game_name": info.get("game_name", ""),
            "title": info.get("title", ""),
            "tags": info.get("tags", []),
            "tweet_tags": current_tweet_tags # ★JSONに追加
        })
    return jsonify({"status": "error"}), 400

@app.route('/force_sync_history', methods=['POST'])
def force_sync_history():
    conf = c.load_config()
    # 別スレッドで実行
    threading.Thread(target=w.sync_vod_history, args=(conf, True)).start()
    return redirect(url_for('analytics_list'))

@app.route('/add_prediction_preset', methods=['POST'])
def add_prediction_preset():
    conf = c.load_config()
    if 'prediction_presets' not in conf: conf['prediction_presets'] = []
    
    raw_outcomes = request.form.getlist('outcomes')
    outcomes = [o.strip() for o in raw_outcomes if o.strip()]
    
    if len(outcomes) < 2:
        return redirect(url_for('index'))

    conf['prediction_presets'].append({
        "title": request.form['title'],
        "outcomes": outcomes,
        "duration": int(request.form['duration'])
    })
    c.save_config(conf)
    return redirect(url_for('index'))

@app.route('/update_prediction_preset', methods=['POST'])
def update_prediction_preset():
    conf = c.load_config()
    idx = int(request.form['preset_index'])
    
    raw_outcomes = request.form.getlist('outcomes')
    outcomes = [o.strip() for o in raw_outcomes if o.strip()]
    
    if 'prediction_presets' in conf and 0 <= idx < len(conf['prediction_presets']) and len(outcomes) >= 2:
        conf['prediction_presets'][idx] = {
            "title": request.form['title'],
            "outcomes": outcomes,
            "duration": int(request.form['duration'])
        }
        c.save_config(conf)
    return redirect(url_for('index'))

@app.route('/delete_prediction_preset/<int:idx>', methods=['POST'])
def delete_prediction_preset(idx):
    conf = c.load_config()
    if 'prediction_presets' in conf and 0 <= idx < len(conf['prediction_presets']):
        conf['prediction_presets'].pop(idx)
        c.save_config(conf)
    return redirect(url_for('index'))

@app.route('/start_prediction', methods=['POST'])
def start_prediction():
    conf = c.load_config()
    idx = int(request.form['preset_index'])
    if 'prediction_presets' in conf:
        preset = conf['prediction_presets'][idx]
        w.create_prediction(conf, preset['title'], preset['outcomes'], preset['duration'])
    return redirect(url_for('index'))

@app.route('/resolve_prediction', methods=['POST'])
def resolve_prediction_route():
    conf = c.load_config()
    pid = request.form['prediction_id']
    oid = request.form.get('winning_outcome_id')
    
    if oid:
        w.resolve_prediction(conf, pid, oid)
    else:
        # キャンセルボタンが押された場合
        w.cancel_prediction(conf, pid)
        
    return redirect(url_for('index'))

@app.route('/delete_debug_history', methods=['POST'])
def delete_debug_history():
    w.delete_history_data('debug_stream')
    return redirect(url_for('index'))

@app.route('/save_config', methods=['POST'])
def save_config_route():
    conf = c.load_config()
    
    # 1. 基本設定の保存
    for k in ['client_id','access_token','broadcaster_id','bot_user_id','channel_name']:
        conf[k] = request.form.get(k)
    conf['broadcaster_token'] = request.form.get('broadcaster_token')
    conf['debug_mode'] = 'debug_mode' in request.form
    conf['ignore_stream_status'] = 'ignore_stream_status' in request.form
    conf['enable_welcome'] = 'enable_welcome' in request.form
    conf['hide_self_bot'] = 'hide_self_bot' in request.form
    conf['enable_vod_download'] = 'enable_vod_download' in request.form
    if 'vod_download_path' in conf: del conf['vod_download_path']
    conf['ignored_users'] = [x.strip().lower() for x in request.form.get('ignored_users_list', '').replace(',', '\n').replace('\r', '').split('\n') if x.strip()]
    
    # 2. レイアウト設定の保存
    if 'layout' not in conf: conf['layout'] = {"cards": {}}
    try:
        conf['layout']['columns'] = int(request.form.get('layout_columns', 2))
        conf['layout']['max_width'] = int(request.form.get('layout_max_width', 1400))
        
        card_keys = ['viewers', 'presets', 'prediction', 'rules', 'logs']
        for key in card_keys:
            if key not in conf['layout']['cards']: conf['layout']['cards'][key] = {}
            conf['layout']['cards'][key]['span'] = int(request.form.get(f'layout_{key}_span', 1))
            
            h_val = request.form.get(f'layout_{key}_height', 0)
            conf['layout']['cards'][key]['height'] = int(h_val) if h_val else 0
            
            # order (表示順) の保存
            order_val = request.form.get(f'layout_{key}_order', 0)
            conf['layout']['cards'][key]['order'] = int(order_val) if order_val else 0

    except Exception as e:
        c.log(f"⚠️ レイアウト設定の保存に失敗: {e}")

    c.save_config(conf)
    return redirect(url_for('index'))

if __name__ == '__main__':
    c.log("Starting Web GUI on port 8501..."); app.run(host='0.0.0.0', port=8501, debug=False)