import time
import requests
from datetime import datetime
import config as c
from services.storage import load_stream_index, save_stream_index, ensure_directories


def get_headers(conf):
    token = conf['access_token'].replace("oauth:", "")
    return {
        'Client-ID': conf['client_id'],
        'Authorization': f"Bearer {token}",
        'Content-Type': 'application/json'
    }


def get_broadcaster_headers(conf):
    token = conf.get('broadcaster_token') or conf['access_token']
    token = token.replace("oauth:", "")
    return {
        'Client-ID': conf['client_id'],
        'Authorization': f"Bearer {token}",
        'Content-Type': 'application/json'
    }


def get_stream_info(conf):
    retries = 3
    for attempt in range(retries):
        try:
            url = f"https://api.twitch.tv/helix/streams?user_id={conf['broadcaster_id']}"
            r = requests.get(url, headers=get_headers(conf), timeout=5)
            if r.status_code == 200:
                if r.json().get('data'):
                    return True, r.json()['data'][0]
                else:
                    return True, None
            c.log(f"⚠️ API check (attempt {attempt+1}) status {r.status_code}")
        except requests.exceptions.RequestException as e:
            c.log(f"⚠️ API check (attempt {attempt+1}) error: {e}")

        if attempt < retries - 1:
            time.sleep(3)

    c.log("❌ API check failed. Network issue?")
    return False, None


def check_stream_status_and_update(conf):
    success, stream_data = get_stream_info(conf)

    if not success:
        return False, None, True

    if stream_data:
        if c.current_stream_id == stream_data['id']:
            idx = load_stream_index()
            if c.current_stream_id in idx:
                updated = False
                if idx[c.current_stream_id].get('title') != stream_data['title']:
                    idx[c.current_stream_id]['title'] = stream_data['title']
                    updated = True
                if idx[c.current_stream_id].get('game_name') != stream_data['game_name']:
                    idx[c.current_stream_id]['game_name'] = stream_data['game_name']
                    updated = True
                thumb = stream_data.get('thumbnail_url', '').replace(
                    '{width}', '%{width}'
                ).replace('{height}', '%{height}')
                if thumb and idx[c.current_stream_id].get('thumbnail_url') != thumb:
                    idx[c.current_stream_id]['thumbnail_url'] = thumb
                    updated = True
                if updated:
                    save_stream_index(idx)
        return True, stream_data, False
    else:
        return False, None, False


def get_total_followers(conf):
    try:
        url = f"https://api.twitch.tv/helix/channels/followers?broadcaster_id={conf['broadcaster_id']}&first=1"
        r = requests.get(url, headers=get_broadcaster_headers(conf), timeout=5)
        if r.status_code == 200:
            return r.json().get('total', 0)
    except (requests.RequestException, KeyError, ValueError):
        pass
    return 0


def get_chatters(conf):
    users = []
    cursor = None
    try:
        while True:
            url = (
                f"https://api.twitch.tv/helix/chat/chatters"
                f"?broadcaster_id={conf['broadcaster_id']}"
                f"&moderator_id={conf['bot_user_id']}&first=1000"
            )
            if cursor:
                url += f"&after={cursor}"
            r = requests.get(url, headers=get_headers(conf), timeout=5)
            if r.status_code != 200:
                return None
            data = r.json()
            users.extend(data['data'])
            cursor = data.get('pagination', {}).get('cursor')
            if not cursor:
                break
        return users
    except (requests.RequestException, KeyError, ValueError):
        return None


def send_chat(conf, message):
    try:
        payload = {
            "broadcaster_id": conf['broadcaster_id'],
            "sender_id": conf['bot_user_id'],
            "message": message
        }
        requests.post(
            'https://api.twitch.tv/helix/chat/messages',
            json=payload, headers=get_headers(conf), timeout=5
        )
        return True
    except (requests.RequestException, KeyError):
        return False


def get_game_id(conf, game_name):
    try:
        url = "https://api.twitch.tv/helix/games"
        params = {"name": game_name}
        r = requests.get(url, headers=get_headers(conf), params=params, timeout=5)
        if r.status_code == 200 and r.json()['data']:
            return r.json()['data'][0]['id']
    except (requests.RequestException, KeyError, IndexError):
        pass
    return None


def search_games(conf, query):
    r = requests.get(
        "https://api.twitch.tv/helix/search/categories",
        headers=get_headers(conf),
        params={"query": query, "first": 10},
        timeout=5
    )
    if r.status_code == 200:
        return r.json().get('data', [])
    return []


def update_channel_info(conf, game_name, title, tags=None):
    try:
        game_id = get_game_id(conf, game_name)
        if not game_id:
            return False
        url = f"https://api.twitch.tv/helix/channels?broadcaster_id={conf['broadcaster_id']}"
        payload = {"game_id": game_id, "title": title}

        if tags is not None and isinstance(tags, list):
            payload["tags"] = tags[:10]

        requests.patch(url, json=payload, headers=get_broadcaster_headers(conf), timeout=10)
        return True
    except (requests.RequestException, KeyError):
        return False


def get_user_id_by_login(conf, login_name):
    try:
        url = f"https://api.twitch.tv/helix/users?login={login_name}"
        r = requests.get(url, headers=get_headers(conf), timeout=5)
        if r.status_code == 200 and r.json()['data']:
            return r.json()['data'][0]['id']
    except (requests.RequestException, KeyError, IndexError):
        pass
    return None


def get_channel_info_by_id(conf, user_id):
    try:
        url = f"https://api.twitch.tv/helix/channels?broadcaster_id={user_id}"
        r = requests.get(url, headers=get_headers(conf), timeout=5)
        if r.status_code == 200 and r.json()['data']:
            return r.json()['data'][0]
    except (requests.RequestException, KeyError, IndexError):
        pass
    return None


def perform_shoutout(conf, target_login_name):
    target_id = get_user_id_by_login(conf, target_login_name)
    if not target_id:
        return False, f"IDが見つかりません: {target_login_name}"
    url = (
        f"https://api.twitch.tv/helix/chat/shoutouts"
        f"?from_broadcaster_id={conf['broadcaster_id']}"
        f"&to_broadcaster_id={target_id}"
        f"&moderator_id={conf['broadcaster_id']}"
    )
    try:
        r = requests.post(url, headers=get_broadcaster_headers(conf), timeout=5)
        if r.status_code == 204:
            info = get_channel_info_by_id(conf, target_id)
            game = info.get('game_name', 'Unknown') if info else 'Unknown'
            send_chat(conf, f"@{target_login_name} 前回は {game} を配信していました！")
            return True, f"📣 公式SO成功: @{target_login_name}"
        return False, f"失敗: {r.status_code}"
    except Exception as e:
        return False, f"エラー: {e}"


def force_update_followers(conf):
    c.log("Debug: 全フォロワーリスト同期開始...")
    all_followers_map = {}
    cursor = None
    endpoint = "https://api.twitch.tv/helix/channels/followers"
    try:
        while True:
            params = [('broadcaster_id', conf['broadcaster_id']), ('first', '100')]
            if cursor:
                params.append(('after', cursor))
            r = requests.get(endpoint, headers=get_broadcaster_headers(conf), params=params, timeout=10)
            if r.status_code != 200:
                return "APIエラー"
            data = r.json()
            for item in data['data']:
                all_followers_map[item['user_id']] = {
                    "followed_at": item['followed_at'].split('T')[0],
                    "name": item['user_name'],
                    "login": item['user_login']
                }
            cursor = data.get('pagination', {}).get('cursor')
            if not cursor:
                break
            time.sleep(0.2)

        with c.file_lock:
            db = c.load_viewers()
            updated_count = 0
            today_str = c.get_now().strftime('%Y-%m-%d')

            for uid, api_data in all_followers_map.items():
                if uid not in db:
                    db[uid] = {
                        "name": api_data['name'], "login": api_data['login'],
                        "is_follower": True, "followed_at": api_data['followed_at'],
                        "unfollowed_at": ""
                    }
                    updated_count += 1
                    c.log_event({
                        "type": "follow", "user": api_data['name'],
                        "followed_at": api_data['followed_at']
                    })
                else:
                    ud = db[uid]
                    if not ud.get("is_follower"):
                        c.log_event({
                            "type": "follow", "user": ud.get("name", api_data['name']),
                            "followed_at": api_data['followed_at']
                        })
                    if (not ud.get("is_follower")
                            or ud.get("followed_at") != api_data['followed_at']
                            or ud.get("unfollowed_at")):
                        ud["is_follower"] = True
                        ud["followed_at"] = api_data['followed_at']
                        ud["unfollowed_at"] = ""
                        updated_count += 1

            for uid, user_data in db.items():
                if user_data.get("is_follower") and uid not in all_followers_map:
                    user_data["is_follower"] = False
                    if not user_data.get("unfollowed_at"):
                        user_data["unfollowed_at"] = today_str
                    updated_count += 1

            if updated_count > 0:
                c.save_viewers(db)

        return f"同期完了: {len(all_followers_map)}人確認、{updated_count}件更新。"
    except Exception as e:
        return f"エラー: {e}"


def sync_vod_history(conf, force_update=False):
    c.log("🔄 過去の配信履歴(VOD)を同期中..." + (" (強制更新モード)" if force_update else ""))
    try:
        url = f"https://api.twitch.tv/helix/videos?user_id={conf['broadcaster_id']}&type=archive&first=100"
        r = requests.get(url, headers=get_headers(conf), timeout=15)
        if r.status_code == 200:
            videos = r.json().get('data', [])
            idx = load_stream_index()
            updated_count = 0

            for v in videos:
                sid = v.get('stream_id')
                if not sid:
                    continue

                if sid not in idx or force_update:
                    old_data = idx.get(sid, {})
                    old_status = old_data.get("vod_status", "not_downloaded")
                    old_path = old_data.get("file_path")
                    old_log_count = old_data.get("log_count", 0)
                    old_max = old_data.get("max_viewers", 0)
                    old_avg_sum = old_data.get("avg_viewers_sum", 0)
                    old_source = old_data.get("source", "api")
                    old_follower_count = old_data.get("follower_count", 0)

                    current_game_name = old_data.get("game_name", "Unknown")

                    idx[sid] = {
                        "start_time": v['created_at'],
                        "title": v['title'],
                        "game_name": current_game_name,
                        "max_viewers": old_max,
                        "avg_viewers_sum": old_avg_sum,
                        "log_count": old_log_count,
                        "avg_viewers": round(old_avg_sum / old_log_count, 1) if old_log_count > 0 else 0,
                        "follower_count": old_follower_count,
                        "source": old_source,
                        "vod_status": old_status,
                        "file_path": old_path,
                        "vod_id": v['id'],
                        "thumbnail_url": v['thumbnail_url'],
                        "duration": v['duration'],
                        "view_count": v['view_count']
                    }
                    if old_path:
                        idx[sid]["file_path"] = old_path
                    updated_count += 1

                elif "vod_id" not in idx[sid]:
                    idx[sid].update({
                        "vod_id": v['id'],
                        "thumbnail_url": v['thumbnail_url'],
                        "duration": v['duration'],
                        "view_count": v['view_count']
                    })
                    updated_count += 1

            if updated_count > 0:
                save_stream_index(idx)
                c.log(f"✅ 配信履歴の同期完了: {updated_count}件を更新しました。")
            else:
                c.log("ℹ️ 更新が必要なデータはありませんでした。")

    except Exception as e:
        c.log(f"⚠️ 履歴同期エラー: {e}")
