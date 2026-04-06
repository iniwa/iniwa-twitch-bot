import copy
import time
import json
import os
import threading
from datetime import datetime, timedelta
import config as c
from services.twitch_api import (
    get_total_followers, get_chatters, check_stream_status_and_update,
    force_update_followers, sync_vod_history, send_chat
)
from services.storage import (
    load_stream_index, save_stream_index, ensure_directories,
    fix_dangling_states, get_formatted_duration, DOWNLOAD_DIR
)
from services.download import auto_download_task, YT_DLP_AVAILABLE
from services.irc import irc_worker

EMPTY_MINUTE_STATS = {
    'messages': [], 'emote_counts': {},
    'subs': {'Prime': 0, 'Tier1': 0, 'Tier2': 0, 'Tier3': 0},
    'gift_subs': 0, 'bits': 0, 'raids': [],
    'point_redemptions': [], 'badges': {},
    'follower_total': 0, 'last_irc_activity': 0,
    'events': []
}

current_minute_stats = copy.deepcopy(EMPTY_MINUTE_STATS)
stats_lock = threading.Lock()

# Intervals (seconds)
VIEWER_POLL_INTERVAL = 20
FOLLOWER_CHECK_INTERVAL = 1800  # 30 minutes
LOG_FLUSH_INTERVAL = 60
OFFLINE_GRACE_COUNT = 3
AUTO_DOWNLOAD_DELAY = 300  # 5 minutes

_shutdown_event = threading.Event()


def _reset_minute_stats():
    """統計を保存後にリセットする (follower_total と last_irc_activity は維持)"""
    preserved = {
        'follower_total': current_minute_stats['follower_total'],
        'last_irc_activity': current_minute_stats['last_irc_activity'],
    }
    for k, v in EMPTY_MINUTE_STATS.items():
        if k in preserved:
            current_minute_stats[k] = preserved[k]
        else:
            current_minute_stats[k] = copy.deepcopy(v)


def flush_logs(conf, stream_data, chatters):
    ensure_directories()
    stream_id = stream_data['id']
    filename = f'data/history/stream_{stream_id}.jsonl'

    db = c.load_viewers()
    census = []
    for u in chatters:
        uid = u['user_id']
        ud = db.get(uid, {})
        census.append({
            'id': uid, 'name': u['user_name'],
            'is_sub': ud.get('is_sub', False),
            'is_follower': ud.get('is_follower', False)
        })

    with stats_lock:
        snapshot = {
            'timestamp': c.get_now().isoformat(),
            'stream_info': {
                'title': stream_data.get('title'),
                'game': stream_data.get('game_name'),
                'tags': stream_data.get('tags', []),
                'follower_total': current_minute_stats.get('follower_total', 0)
            },
            'metrics': {
                'viewer_count': stream_data.get('viewer_count', 0),
                'chat_count': len(chatters),
                'msg_speed': len(current_minute_stats['messages']),
                'bits': current_minute_stats['bits'],
                'gift_subs': current_minute_stats['gift_subs']
            },
            'emotes': current_minute_stats['emote_counts'].copy(),
            'subs': current_minute_stats['subs'].copy(),
            'raids': current_minute_stats['raids'][:],
            'points': current_minute_stats['point_redemptions'][:],
            'badges': current_minute_stats['badges'].copy(),
            'messages': current_minute_stats['messages'][:],
            'events': current_minute_stats['events'][:],
            'census': census
        }
        _reset_minute_stats()

    with open(filename, 'a', encoding='utf-8') as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + '\n')

    idx = load_stream_index()
    if stream_id not in idx:
        idx[stream_id] = {
            'start_time': stream_data.get('started_at'),
            'title': stream_data.get('title'),
            'game_name': stream_data.get('game_name'),
            'max_viewers': 0, 'avg_viewers_sum': 0, 'log_count': 0,
            'vod_status': 'not_downloaded', 'source': 'bot'
        }

    item = idx[stream_id]
    viewer_count = stream_data.get('viewer_count', 0)
    item['max_viewers'] = max(item.get('max_viewers', 0), viewer_count)
    item['avg_viewers_sum'] = item.get('avg_viewers_sum', 0) + viewer_count
    item['log_count'] = item.get('log_count', 0) + 1
    item['source'] = 'bot'
    item['avg_viewers'] = round(item['avg_viewers_sum'] / item['log_count'], 1)
    item['title'] = stream_data.get('title')
    item['game_name'] = stream_data.get('game_name')
    item['follower_count'] = current_minute_stats.get('follower_total', 0)
    save_stream_index(idx)


def get_debug_status():
    with stats_lock:
        last_activity = current_minute_stats['last_irc_activity']
        return {
            'irc_connected': (time.time() - last_activity) < 300,
            'last_irc_msg': (
                datetime.fromtimestamp(last_activity, c.JST).strftime('%H:%M:%S')
                if last_activity else 'なし'
            ),
            'buffered_messages': len(current_minute_stats['messages']),
            'current_follower_count': current_minute_stats['follower_total'],
            'log_path_exists': os.path.exists('data/history'),
            'yt_dlp_installed': YT_DLP_AVAILABLE,
            'download_dir': DOWNLOAD_DIR
        }


def _handle_stream_end(conf, finished_id):
    """配信終了時の処理"""
    c.log('[END] 配信終了検知')

    idx = load_stream_index()
    if finished_id in idx:
        entry = idx[finished_id]
        st_str = entry.get('start_time')
        if st_str:
            start_dt = c.parse_iso_jst(st_str)
            if start_dt:
                try:
                    end_dt = c.get_now()
                    duration_sec = (end_dt - start_dt).total_seconds()
                    entry['duration'] = get_formatted_duration(duration_sec)
                    save_stream_index(idx)
                    c.log(f'[TIME] 配信時間確定: {entry["duration"]}')
                except Exception as e:
                    c.log(f'[WARN] 時間計算エラー: {e}')
            else:
                c.log('[WARN] 時間計算エラー: start_time の解析に失敗')

    if conf.get('enable_vod_download'):
        threading.Thread(
            target=auto_download_task, args=(conf, finished_id), daemon=True
        ).start()

    c.current_session_viewers.clear()
    c.current_stream_id = None
    c.current_game = None
    c.state.reset()


def _update_chatters(conf, chatters, stream_id):
    """視聴者リストの更新"""
    with c.file_lock:
        current_chatter_ids = set()
        db = c.load_viewers()
        now_ts = int(time.time())
        now_dt = c.get_now()
        updated = False

        for u in chatters:
            uid = u['user_id']
            current_chatter_ids.add(uid)

            if uid not in c.current_session_viewers:
                c.current_session_viewers[uid] = {
                    'joined_at': now_dt,
                    'name': u['user_name'],
                    'login': u['user_login']
                }

            if uid not in db:
                db[uid] = {
                    'name': u['user_name'],
                    'login': u['user_login'],
                    'total_visits': 0, 'streak': 0
                }

            ud = db[uid]
            ud['total_duration'] = ud.get('total_duration', 0) + 20

            if ud.get('last_stream_id') != stream_id:
                ud['total_visits'] = ud.get('total_visits', 0) + 1
                if (ud.get('last_seen_ts', 0) > 0
                        and (now_ts - ud['last_seen_ts']) < 129600):
                    ud['streak'] = ud.get('streak', 0) + 1
                else:
                    ud['streak'] = 1
                ud['last_stream_id'] = stream_id

            ud['last_seen_ts'] = now_ts
            updated = True

        # 退出した視聴者をセッションから除去
        for existing_uid in list(c.current_session_viewers.keys()):
            if existing_uid not in current_chatter_ids:
                del c.current_session_viewers[existing_uid]

        if updated:
            c.save_viewers(db)


def viewer_worker_loop(conf):
    c.log('Viewer/Monitor Worker Started')
    fix_dangling_states()

    last_log_time = time.time()
    last_follow_check = 0
    offline_streak = 0
    ensure_directories()

    try:
        if conf.get('broadcaster_id'):
            sync_vod_history(conf)
    except Exception as e:
        c.log(f'[WARN] 初期VOD同期エラー: {e}')

    time.sleep(5)
    while not _shutdown_event.is_set():
        try:
            conf = c.load_config()

            # 定期的なフォロワー/VOD同期 (30分ごと)
            if conf.get('is_running') and (time.time() - last_follow_check > FOLLOWER_CHECK_INTERVAL):
                force_update_followers(conf)
                sync_vod_history(conf)
                last_follow_check = time.time()

            if not conf.get('is_running'):
                time.sleep(10)
                continue

            # フォロワー数取得 (約1分ごと)
            if time.time() - last_log_time >= 55:
                ft = get_total_followers(conf)
                if ft > 0:
                    with stats_lock:
                        current_minute_stats['follower_total'] = ft

            is_live, stream_data, is_error = check_stream_status_and_update(conf)

            if is_error:
                c.log('[WARN] API接続エラー: 状態を維持して再試行します')
                time.sleep(VIEWER_POLL_INTERVAL)
                continue

            # オフライン猶予 (3回連続でオフラインなら確定)
            if not is_live and c.current_stream_id is not None:
                offline_streak += 1
                if offline_streak < OFFLINE_GRACE_COUNT:
                    c.log(f'[WARN] オフライン検知 ({offline_streak}/3) - 待機中...')
                    time.sleep(VIEWER_POLL_INTERVAL)
                    continue
            else:
                offline_streak = 0

            stream_id = stream_data['id'] if is_live and stream_data else None

            # デバッグモード
            if conf.get('ignore_stream_status') and not is_live:
                is_live = True
                stream_id = 'debug_stream'
                stream_data = {
                    'id': 'debug_stream', 'title': 'Debug',
                    'game_name': 'Debug', 'viewer_count': 0,
                    'started_at': c.get_now().isoformat()
                }

            if not is_live:
                if c.current_stream_id is not None:
                    _handle_stream_end(conf, c.current_stream_id)
                time.sleep(VIEWER_POLL_INTERVAL)
                continue

            # 配信開始検知
            if c.current_stream_id != stream_id:
                c.current_stream_id = stream_id
                c.current_game = stream_data.get('game_name')
                c.current_session_viewers.clear()
                c.state.reset()
                c.log(f'[START] 配信開始検知: {stream_data.get("title")} ({c.current_game})')

                idx = load_stream_index()
                if stream_id not in idx:
                    idx[stream_id] = {
                        'start_time': stream_data.get('started_at'),
                        'title': stream_data.get('title'),
                        'game_name': stream_data.get('game_name'),
                        'max_viewers': 0, 'avg_viewers_sum': 0, 'log_count': 0,
                        'source': 'bot', 'vod_status': 'not_downloaded',
                        'thumbnail_url': stream_data.get('thumbnail_url', '').replace(
                            '{width}', '%{width}'
                        ).replace('{height}', '%{height}')
                    }
                    save_stream_index(idx)
            else:
                c.current_game = stream_data.get('game_name')

            chatters = get_chatters(conf)
            if chatters is None:
                time.sleep(VIEWER_POLL_INTERVAL)
                continue

            _update_chatters(conf, chatters, stream_id)

            if time.time() - last_log_time >= LOG_FLUSH_INTERVAL:
                flush_logs(conf, stream_data, chatters)
                last_log_time = time.time()

            time.sleep(VIEWER_POLL_INTERVAL)
        except Exception as e:
            c.log(f'Viewer Err: {e}')
            time.sleep(VIEWER_POLL_INTERVAL)


def bot_worker():
    c.log('Bot Worker Started')
    while not _shutdown_event.is_set():
        try:
            time.sleep(10)
            conf = c.load_config()
            if not conf.get('is_running'):
                continue
            if not c.current_stream_id and not conf.get('ignore_stream_status'):
                continue

            current_comments = c.state.get_count()
            now = c.get_now()

            for i, rule in enumerate(conf.get('rules', [])):
                g = rule['game']
                is_target = (
                    (g == 'All')
                    or (g == 'Default' and not any(
                        r['game'] == c.current_game
                        for r in conf.get('rules', [])
                        if r['game'] not in ('All', 'Default')
                    ))
                    or (g == c.current_game)
                )
                if not is_target:
                    continue

                last = c.rule_last_executed.get(i, datetime.min.replace(tzinfo=c.JST))
                if (now - last) < timedelta(minutes=int(rule.get('interval', 15))):
                    continue
                if (current_comments - c.rule_last_comment_count.get(i, 0)) < int(rule.get('min_comments', 2)):
                    continue

                if send_chat(conf, rule['message']):
                    c.log(f'Sent: {rule["message"]}')
                    c.rule_last_executed[i] = now
                    c.rule_last_comment_count[i] = current_comments
                    time.sleep(1.0)
                    break
        except Exception as e:
            c.log(f'Bot Err: {e}')
            time.sleep(60)


_worker_threads = []

def start_workers():
    conf = c.load_config()
    _worker_threads.clear()
    t1 = threading.Thread(target=viewer_worker_loop, args=(conf,), daemon=True, name='viewer-worker')
    t2 = threading.Thread(target=bot_worker, daemon=True, name='bot-worker')
    t3 = threading.Thread(target=irc_worker, args=(stats_lock, current_minute_stats, _shutdown_event), daemon=True, name='irc-worker')
    for t in [t1, t2, t3]:
        t.start()
        _worker_threads.append(t)


def shutdown_workers():
    _shutdown_event.set()
    for t in _worker_threads:
        t.join(timeout=5)
