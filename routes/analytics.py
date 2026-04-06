from flask import Blueprint, render_template
from datetime import datetime
import json
import os
import re
import config as c
from services.storage import ARCHIVE_WAIT_DIR, ARCHIVE_ENCODE_DIR

bp = Blueprint('analytics', __name__)


def _validate_stream_id(stream_id):
    """stream_id がパストラバーサルを含まない安全な値か検証"""
    return bool(stream_id and re.match(r'^[a-zA-Z0-9_-]+$', stream_id))


@bp.route('/analytics')
def analytics_list():
    index_file = 'data/history/stream_index.json'
    index_data = {}
    sorted_list = []

    if os.path.exists(index_file):
        try:
            with c.file_lock:
                with open(index_file, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)

            for sid, data in index_data.items():
                data['sid'] = sid
                if data.get('vod_status') == 'downloaded' and data.get('file_path'):
                    fp = data['file_path']
                    fname = os.path.basename(fp)
                    encode_path = os.path.join(ARCHIVE_ENCODE_DIR, fname)
                    wait_path = os.path.join(ARCHIVE_WAIT_DIR, fname)
                    if os.path.exists(encode_path):
                        data['encode_status'] = 'encoded'
                        data['archive_file_size'] = os.path.getsize(encode_path)
                    elif os.path.exists(wait_path):
                        data['encode_status'] = 'waiting'
                        data['archive_file_size'] = os.path.getsize(wait_path)
                    elif os.path.exists(fp):
                        data['encode_status'] = 'waiting'
                        data['archive_file_size'] = os.path.getsize(fp)
                    else:
                        data['encode_status'] = 'missing'
                        data['archive_file_size'] = 0

                if data.get('start_time'):
                    try:
                        dt = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
                        dt_jst = dt.astimezone(c.JST)
                        data['start_time'] = dt_jst.isoformat()
                    except (ValueError, TypeError):
                        pass

                dur = data.get('duration', '')
                if dur and 'm' in dur and 's' in dur:
                    data['duration_short'] = re.sub(r'\d+s$', '', dur)
                else:
                    data['duration_short'] = dur
                sorted_list.append(data)
            sorted_list.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        except (json.JSONDecodeError, OSError):
            pass

    viewers = c.load_viewers()
    daily_changes = {}

    for uid, data in viewers.items():
        if data.get("followed_at"):
            f_date = data["followed_at"]
            daily_changes[f_date] = daily_changes.get(f_date, 0) + 1
        if data.get("unfollowed_at"):
            u_date = data["unfollowed_at"]
            daily_changes[u_date] = daily_changes.get(u_date, 0) - 1

    follower_history = []
    current_count = 0

    if daily_changes:
        sorted_dates = sorted(daily_changes.keys())
        for d_str in sorted_dates:
            current_count += daily_changes[d_str]
            if current_count < 0:
                current_count = 0
            follower_history.append({"x": d_str, "y": current_count})

        today_str = c.get_now().strftime('%Y-%m-%d')
        if sorted_dates[-1] < today_str:
            follower_history.append({"x": today_str, "y": current_count})

        # 配信日をデータポイントとして追加（変化点以外の配信タイミングも記録）
        existing_dates = {p["x"] for p in follower_history}
        for s in sorted_list:
            sd = (s.get('start_time') or '')[:10]
            if sd and sd not in existing_dates:
                count_at_date = 0
                for p in follower_history:
                    if p["x"] <= sd:
                        count_at_date = p["y"]
                    else:
                        break
                follower_history.append({"x": sd, "y": count_at_date})
                existing_dates.add(sd)
        follower_history.sort(key=lambda p: p["x"])

    follower_history_json = json.dumps(follower_history, ensure_ascii=False)
    all_streams_json = json.dumps(sorted_list, ensure_ascii=False)

    return render_template(
        'analytics_list.html',
        view='list', index_data=index_data,
        sorted_list=sorted_list,
        all_streams_json=all_streams_json,
        follower_history_json=follower_history_json
    )


@bp.route('/analytics/stream/<stream_id>')
def analytics_detail(stream_id):
    if not _validate_stream_id(stream_id):
        return render_template('analytics_detail.html', view='detail',
                             stream_info={"title": "不正なID", "start_time": None},
                             has_log_file=False)
    index_file = 'data/history/stream_index.json'
    stream_info = {"title": "不明", "start_time": None}
    if os.path.exists(index_file):
        try:
            with c.file_lock:
                with open(index_file, 'r', encoding='utf-8') as f:
                    stream_info = json.load(f).get(stream_id, stream_info)
        except (json.JSONDecodeError, OSError):
            pass

    log_file = f"data/history/stream_{stream_id}.jsonl"
    chart_labels, chart_viewers, chart_comments = [], [], []
    chat_logs, total_emote_counts, unique_chatters = [], {}, set()
    event_logs = []
    total_comments = 0
    max_viewers = 0
    sum_viewers = 0
    count_logs = 0
    total_subs = 0
    total_gift_subs = 0
    total_bits = 0
    total_points = 0

    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        dt = datetime.fromisoformat(data.get("timestamp", ""))
                        time_label = dt.strftime('%H:%M')
                        v = data['metrics']['viewer_count']
                        c_spd = data['metrics']['msg_speed']
                        chart_labels.append(time_label)
                        chart_viewers.append(v)
                        chart_comments.append(c_spd)
                        max_viewers = max(max_viewers, v)
                        sum_viewers += v
                        count_logs += 1
                        total_bits += data['metrics'].get('bits', 0)
                        subs = data.get('subs', {})
                        total_subs += sum(subs.values())
                        total_gift_subs += data['metrics'].get('gift_subs', 0)
                        for ev in data.get("events", []):
                            ev["time"] = time_label
                            event_logs.append(ev)
                        for msg in data.get("messages", []):
                            chat_logs.append({
                                "type": "msg", "time": time_label,
                                "user": msg['user'], "text": msg['text'],
                                "is_sub": msg['is_sub']
                            })
                            unique_chatters.add(msg['user'])
                            total_comments += 1
                        for pt in data.get("points", []):
                            chat_logs.append({
                                "type": "point", "time": time_label,
                                "user": pt['user'],
                                "reward_id": pt['reward_id'],
                                "text": pt.get('text', '')
                            })
                            total_points += 1
                        for eid, cnt in data.get("emotes", {}).items():
                            total_emote_counts[eid] = total_emote_counts.get(eid, 0) + cnt
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass
        except OSError:
            pass

        avg_viewers = round(sum_viewers / count_logs, 1) if count_logs > 0 else 0
        top_emotes = sorted(total_emote_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        stats = {
            "max_viewers": max_viewers,
            "avg_viewers": avg_viewers,
            "total_comments": total_comments,
            "unique_chatters": len(unique_chatters),
            "total_subs": total_subs,
            "total_gift_subs": total_gift_subs,
            "total_bits": total_bits,
            "total_points": total_points
        }
        return render_template(
            'analytics_detail.html',
            view='detail', stream_info=stream_info,
            has_log_file=True, stats=stats,
            chart_labels=chart_labels,
            chart_viewers=chart_viewers,
            chart_comments=chart_comments,
            chat_logs=chat_logs,
            event_logs=event_logs,
            top_emotes=top_emotes
        )
    else:
        return render_template(
            'analytics_detail.html',
            view='detail', stream_info=stream_info,
            has_log_file=False
        )
