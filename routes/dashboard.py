from flask import Blueprint, render_template, jsonify, request
from datetime import datetime, timedelta
import config as c
from services.predictions import get_current_prediction

bp = Blueprint('dashboard', __name__)


def _timestamp_to_date(ts):
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, c.JST).strftime('%Y-%m-%d %H:%M')


def get_history_api_data():
    conf = c.load_config()
    db = c.load_viewers()
    active_uids = set(c.current_session_viewers.keys())
    ignored_ids = (
        [conf.get('broadcaster_id'), conf.get('bot_user_id')]
        if conf.get('hide_self_bot') else []
    )
    ignored_logins = set(conf.get('ignored_users', []))
    data_list = []
    for uid, v in db.items():
        if uid in ignored_ids:
            continue
        if v.get('login', '').lower() in ignored_logins:
            continue
        follow_sort = (
            v["followed_at"].replace("-", "")
            if (v.get("is_follower") and v.get("followed_at")) else 0
        )
        dur_val = v.get("total_duration", 0)
        h, rem = divmod(dur_val, 3600)
        m, s = divmod(rem, 60)
        dur_str = f"{h}h {m}m" if h > 0 else f"{m}m"
        is_active = uid in active_uids
        ls_val = v.get("last_seen_ts", 0)
        ls_sort = 9999999999 if is_active else ls_val
        ls_str = (
            '<span class="watching-status">視聴中</span>'
            if is_active else _timestamp_to_date(ls_val)
        )
        data_list.append({
            "uid": uid, "name": v.get("name", ""),
            "login": v.get("login", "") or "-",
            "is_follower": v.get("is_follower", False),
            "followed_at": v.get("followed_at", ""),
            "follow_sort": follow_sort,
            "total_visits": v.get("total_visits", 0),
            "total_duration": dur_str,
            "total_duration_raw": dur_val,
            "total_comments": v.get("total_comments", 0),
            "total_bits": v.get("total_bits", 0),
            "is_sub": v.get("is_sub", False),
            "total_sub_months": v.get("total_sub_months", 0),
            "total_gifts_given": v.get("total_gifts_given", 0),
            "total_gifts_received": v.get("total_gifts_received", 0),
            "streak": v.get("streak", 0),
            "memo": v.get("memo", ""),
            "memo_esc": v.get("memo", "").replace("'", "\\'"),
            "last_seen_sort": ls_sort,
            "last_seen_str": ls_str
        })
    return data_list


def get_active_viewers_data():
    conf = c.load_config()
    db = c.load_viewers()
    now = c.get_now()
    active_viewers = []
    ignored_ids = (
        [conf.get('broadcaster_id'), conf.get('bot_user_id')]
        if conf.get('hide_self_bot') else []
    )
    ignored_logins = set(conf.get('ignored_users', []))
    for uid, info in c.current_session_viewers.items():
        if uid in ignored_ids:
            continue
        if info.get('login', '').lower() in ignored_logins:
            continue
        ud = db.get(uid, {"total_visits": 1})
        d = now - info['joined_at']
        h, rem = divmod(d.seconds, 3600)
        m, s = divmod(rem, 60)
        follow_str = (
            f'<span class="follow-status">✅</span>'
            f' <span style="font-size:0.75em; color:#888;">{ud.get("followed_at", "")}</span>'
            if ud.get("is_follower") else "-"
        )
        active_viewers.append({
            "uid": uid, "name": info['name'],
            "login": info.get('login', ''),
            "duration": f"{h}h {m}m" if h else f"{m}m",
            "total": ud.get('total_visits', 1),
            "streak": ud.get('streak', 0),
            "follow_status": follow_str,
            "memo": ud.get("memo", "")
        })
    return active_viewers


@bp.route('/')
def index():
    conf = c.load_config()
    db = c.load_viewers()
    active_viewers = get_active_viewers_data()
    ignored_ids = (
        [conf.get('broadcaster_id'), conf.get('bot_user_id')]
        if conf.get('hide_self_bot') else []
    )
    ignored_logins = set(conf.get('ignored_users', []))
    active_uids = set(c.current_session_viewers.keys())
    filtered_history = {
        k: v for k, v in db.items()
        if k not in ignored_ids and v.get('login', '').lower() not in ignored_logins
    }
    current_game = c.current_game if c.current_game else ""
    unique_games = sorted(list(set(
        [p['game'] for p in conf.get('presets', [])]
        + [r['game'] for r in conf.get('rules', [])]
    )))
    current_total_comments = c.state.get_count()
    now = c.get_now()

    for i, rule in enumerate(conf.get("rules", [])):
        rule["is_active"] = False
        rule["reason"] = "-"
        has_specific = any(
            r["game"] == current_game
            for r in conf.get("rules", [])
            if r["game"] not in ["All", "Default"]
        )
        g = rule["game"]
        if g == "All":
            rule["is_active"] = True
        elif g == "Default":
            rule["is_active"] = not has_specific
            rule["reason"] = "専用優先" if has_specific else ""
        elif g == current_game:
            rule["is_active"] = True
        else:
            rule["reason"] = "不一致"

        if rule["is_active"]:
            last = c.rule_last_executed.get(i, datetime.min.replace(tzinfo=c.JST))
            last_count = c.rule_last_comment_count.get(i, 0)
            diff = current_total_comments - last_count
            remaining = int(rule.get("min_comments", 2)) - diff
            rule["remaining_comments"] = max(0, remaining)
            if last == datetime.min.replace(tzinfo=c.JST):
                rule["next_run"] = "すぐ"
            else:
                mins = int((
                    (last + timedelta(minutes=int(rule.get("interval", 15)))) - now
                ).total_seconds() / 60)
                rule["next_run"] = "条件待ち" if mins < 0 else f"あと約{mins}分"

    current_prediction = get_current_prediction(conf)

    return render_template(
        'dashboard.html',
        config=conf, logs=c.logs,
        active_viewers=active_viewers,
        history=filtered_history,
        active_uids=active_uids,
        current_game=current_game,
        unique_games=unique_games,
        current_prediction=current_prediction,
        now=now
    )


def get_rules_status():
    conf = c.load_config()
    current_game = c.current_game if c.current_game else ""
    current_total_comments = c.state.get_count()
    now = c.get_now()
    has_specific = any(
        r["game"] == current_game
        for r in conf.get("rules", [])
        if r["game"] not in ["All", "Default"]
    )
    rules = []
    for i, rule in enumerate(conf.get("rules", [])):
        g = rule["game"]
        is_active = False
        reason = "-"
        if g == "All":
            is_active = True
        elif g == "Default":
            is_active = not has_specific
            reason = "専用優先" if has_specific else ""
        elif g == current_game:
            is_active = True
        else:
            reason = "不一致"
        entry = {
            "index": i,
            "name": rule.get("name", ""),
            "message": rule.get("message", ""),
            "game": rule.get("game", ""),
            "is_active": is_active,
            "reason": reason,
            "remaining_comments": 0,
            "next_run": "",
            "next_run_mins": 0,
        }
        if is_active:
            last = c.rule_last_executed.get(i, datetime.min.replace(tzinfo=c.JST))
            last_count = c.rule_last_comment_count.get(i, 0)
            diff = current_total_comments - last_count
            remaining = int(rule.get("min_comments", 2)) - diff
            entry["remaining_comments"] = max(0, remaining)
            if last == datetime.min.replace(tzinfo=c.JST):
                entry["next_run"] = "すぐ"
                entry["next_run_mins"] = 0
            else:
                mins = int(((last + timedelta(minutes=int(rule.get("interval", 15)))) - now).total_seconds() / 60)
                entry["next_run"] = "条件待ち" if mins < 0 else f"あと約{mins}分"
                entry["next_run_mins"] = max(0, mins)
        rules.append(entry)
    return rules


@bp.route('/api/status')
def api_status():
    conf = c.load_config()
    return jsonify({
        "logs": c.logs,
        "viewers": get_active_viewers_data(),
        "count": len(c.current_session_viewers),
        "viewer_count": len(c.current_session_viewers),
        "is_live": c.current_stream_id is not None,
        "current_game": c.current_game or '',
        "current_title": conf.get('current_title', ''),
        "rules_status": get_rules_status(),
        "total_comments": c.state.get_count(),
        "events": c.events[:50],
    })


@bp.route('/api/history')
def api_history():
    return jsonify(get_history_api_data())


@bp.route('/api/current_settings')
def api_current_settings():
    conf = c.load_config()
    from services.twitch_api import get_channel_info_by_id
    info = get_channel_info_by_id(conf, conf['broadcaster_id'])
    current_tweet_tags = conf.get('current_tweet_tags', '')
    if info:
        return jsonify({
            "status": "success",
            "game_name": info.get("game_name", ""),
            "title": info.get("title", ""),
            "tags": info.get("tags", []),
            "tweet_tags": current_tweet_tags
        })
    return jsonify({"status": "error"}), 400


@bp.route('/api/search_games')
def api_search_games():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    try:
        conf = c.load_config()
        from services.twitch_api import search_games
        return jsonify(search_games(conf, q))
    except Exception as e:
        c.log(f"⚠️ ゲーム検索エラー: {e}")
        return jsonify([]), 500
