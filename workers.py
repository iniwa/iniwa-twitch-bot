import time
import socket
import requests
import threading
import os
import glob
import json
import re
from datetime import datetime, timedelta
import config as c # c.get_now(), c.JST を使用

# --- yt-dlp (動画ダウンロード用) ---
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False

# --- 定数 ---
DOWNLOAD_DIR = '/app/downloads'

# --- グローバル変数 ---
current_minute_stats = {
    "messages": [], "emote_counts": {},
    "subs": {"Prime":0, "Tier1":0, "Tier2":0, "Tier3":0},
    "gift_subs": 0, "bits": 0, "raids": [],
    "point_redemptions": [], "badges": {},
    "follower_total": 0, "last_irc_activity": 0
}
download_progress = {} 
cancel_requests = set()

stats_lock = threading.Lock()

# --- ユーティリティ ---
def ensure_directories():
    if not os.path.exists('data/history'): os.makedirs('data/history')
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)

def load_stream_index():
    path = 'data/history/stream_index.json'
    if not os.path.exists(path): return {}
    with c.file_lock:
        try:
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {}

def save_stream_index(data):
    ensure_directories()
    with c.file_lock:
        with open('data/history/stream_index.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

def get_formatted_duration(seconds):
    if not seconds: return "0m"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h > 0: return f"{h}h{m}m{s}s"
    return f"{m}m{s}s"

def cleanup_temp_files(save_path, filename_base):
    if not filename_base: return
    patterns = [
        f"{filename_base}.mp4.part",
        f"{filename_base}.mp4.ytdl",
        f"{filename_base}.part",
        f"{filename_base}.ytdl"
    ]
    for pat in patterns:
        full_path = os.path.join(save_path, pat)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
                c.log(f"🧹 ゴミファイルを削除しました: {pat}")
            except Exception as e:
                c.log(f"⚠️ ゴミ削除失敗: {pat} ({e})")

# --- 起動時の不整合修復 ---
def fix_dangling_states():
    c.log("🔧 起動時チェック: ダウンロード状態の整合性を確認中...")
    with c.file_lock:
        idx = load_stream_index()
        modified = False
        save_path = DOWNLOAD_DIR
        
        for sid, data in idx.items():
            if data.get("vod_status") == "downloading":
                # ファイル名の構築 (JST変換)
                file_date = "00000000"
                if "start_time" in data and data["start_time"]:
                    try:
                        # UTC文字 -> UTC datetime -> JST datetime
                        dt_obj = datetime.fromisoformat(data["start_time"].replace('Z', '+00:00')).astimezone(c.JST)
                        file_date = dt_obj.strftime('%Y%m%d')
                    except: pass
                
                raw_title = data.get("title", "Untitled")
                file_title = re.sub(r'[\\/:*?"<>|]', '_', raw_title)
                expected_filename = f"{file_date}_{file_title}.mp4"
                full_path = os.path.join(save_path, expected_filename)
                
                if os.path.exists(full_path):
                    c.log(f"✅ 復旧: {expected_filename} を確認。ステータスを[保存済]に修正します。")
                    data["vod_status"] = "downloaded"
                    data["file_path"] = full_path
                    if "vod_id" not in data: data["vod_id"] = sid 
                    modified = True
                else:
                    c.log(f"⚠️ リセット: {sid} の完了ファイルがありません。ステータスを戻し、ゴミを掃除します。")
                    cleanup_temp_files(save_path, f"{file_date}_{file_title}")
                    data["vod_status"] = "not_downloaded"
                    modified = True
        
        if modified:
            save_stream_index(idx)

# --- API関連 ---
def get_headers(conf):
    token = conf['access_token'].replace("oauth:", "")
    return {'Client-ID': conf['client_id'], 'Authorization': f"Bearer {token}", 'Content-Type': 'application/json'}

def get_broadcaster_headers(conf):
    token = conf.get('broadcaster_token') or conf['access_token']
    token = token.replace("oauth:", "")
    return {'Client-ID': conf['client_id'], 'Authorization': f"Bearer {token}", 'Content-Type': 'application/json'}

def get_stream_info(conf):
    try:
        url = f"https://api.twitch.tv/helix/streams?user_id={conf['broadcaster_id']}"
        r = requests.get(url, headers=get_headers(conf), timeout=5)
        if r.status_code == 200 and r.json()['data']: 
            return True, r.json()['data'][0]
    except: pass
    return False, None

def check_stream_status_and_update(conf):
    is_live, stream_data = get_stream_info(conf)
    if is_live and stream_data and c.current_stream_id == stream_data['id']:
        idx = load_stream_index()
        if c.current_stream_id in idx:
            updated = False
            if idx[c.current_stream_id].get('title') != stream_data['title']:
                idx[c.current_stream_id]['title'] = stream_data['title']; updated = True
            if idx[c.current_stream_id].get('game_name') != stream_data['game_name']:
                idx[c.current_stream_id]['game_name'] = stream_data['game_name']; updated = True
            thumb = stream_data.get('thumbnail_url', '').replace('{width}', '%{width}').replace('{height}', '%{height}')
            if thumb and idx[c.current_stream_id].get('thumbnail_url') != thumb:
                idx[c.current_stream_id]['thumbnail_url'] = thumb; updated = True
            if updated: save_stream_index(idx)
    return is_live, stream_data

def get_total_followers(conf):
    try:
        url = f"https://api.twitch.tv/helix/channels/followers?broadcaster_id={conf['broadcaster_id']}&first=1"
        r = requests.get(url, headers=get_broadcaster_headers(conf), timeout=5)
        if r.status_code == 200: return r.json().get('total', 0)
    except: pass
    return 0

def get_chatters(conf):
    users = []
    cursor = None
    try:
        while True:
            url = f"https://api.twitch.tv/helix/chat/chatters?broadcaster_id={conf['broadcaster_id']}&moderator_id={conf['bot_user_id']}&first=1000"
            if cursor: url += f"&after={cursor}"
            r = requests.get(url, headers=get_headers(conf), timeout=5)
            if r.status_code != 200: return None
            data = r.json()
            users.extend(data['data'])
            cursor = data.get('pagination', {}).get('cursor')
            if not cursor: break
        return users
    except: return None

def send_chat(conf, message):
    try:
        payload = {"broadcaster_id": conf['broadcaster_id'], "sender_id": conf['bot_user_id'], "message": message}
        requests.post('https://api.twitch.tv/helix/chat/messages', json=payload, headers=get_headers(conf), timeout=5)
        return True
    except: return False

def get_game_id(conf, game_name):
    try:
        url = "https://api.twitch.tv/helix/games"; params = {"name": game_name}
        r = requests.get(url, headers=get_headers(conf), params=params, timeout=5)
        if r.status_code == 200 and r.json()['data']: return r.json()['data'][0]['id']
    except: pass
    return None

def update_channel_info(conf, game_name, title, tags=None):
    try:
        game_id = get_game_id(conf, game_name)
        if not game_id: return False
        url = f"https://api.twitch.tv/helix/channels?broadcaster_id={conf['broadcaster_id']}"
        
        payload = {"game_id": game_id, "title": title}
        
        # タグが指定されている場合のみpayloadに追加 (最大10個)
        if tags is not None and isinstance(tags, list):
            payload["tags"] = tags[:10]

        requests.patch(url, json=payload, headers=get_broadcaster_headers(conf), timeout=10)
        return True
    except: return False

def get_user_id_by_login(conf, login_name):
    try:
        url = f"https://api.twitch.tv/helix/users?login={login_name}"
        r = requests.get(url, headers=get_headers(conf), timeout=5)
        if r.status_code == 200 and r.json()['data']: return r.json()['data'][0]['id']
    except: pass
    return None

def get_channel_info_by_id(conf, user_id):
    try:
        url = f"https://api.twitch.tv/helix/channels?broadcaster_id={user_id}"
        r = requests.get(url, headers=get_headers(conf), timeout=5)
        if r.status_code == 200 and r.json()['data']: return r.json()['data'][0] 
    except: pass
    return None

def perform_shoutout(conf, target_login_name):
    target_id = get_user_id_by_login(conf, target_login_name)
    if not target_id: return False, f"IDが見つかりません: {target_login_name}"
    url = f"https://api.twitch.tv/helix/chat/shoutouts?from_broadcaster_id={conf['broadcaster_id']}&to_broadcaster_id={target_id}&moderator_id={conf['broadcaster_id']}"
    try:
        r = requests.post(url, headers=get_broadcaster_headers(conf), timeout=5)
        if r.status_code == 204:
            info = get_channel_info_by_id(conf, target_id)
            game = info.get('game_name','Unknown') if info else 'Unknown'
            send_chat(conf, f"@{target_login_name} 前回は {game} を配信していました！")
            return True, f"📣 公式SO成功: @{target_login_name}"
        return False, f"失敗: {r.status_code}"
    except Exception as e: return False, f"エラー: {e}"

def force_update_followers(conf):
    c.log("Debug: 全フォロワーリスト同期開始...")
    all_followers_map = {}
    cursor = None
    endpoint = "https://api.twitch.tv/helix/channels/followers"
    try:
        while True:
            params = [('broadcaster_id', conf['broadcaster_id']), ('first', '100')]
            if cursor: params.append(('after', cursor))
            r = requests.get(endpoint, headers=get_broadcaster_headers(conf), params=params, timeout=10)
            if r.status_code != 200: return "APIエラー"
            data = r.json()
            for item in data['data']:
                all_followers_map[item['user_id']] = {
                    "followed_at": item['followed_at'].split('T')[0], # これは日付文字列なのでそのまま
                    "name": item['user_name'], "login": item['user_login']
                }
            cursor = data.get('pagination', {}).get('cursor')
            if not cursor: break
            time.sleep(0.2)
        
        with c.file_lock:
            db = c.load_viewers() 
            updated_count = 0
            # ★変更: JSTの日付文字列を使用
            today_str = c.get_now().strftime('%Y-%m-%d')
            
            for uid, api_data in all_followers_map.items():
                if uid not in db:
                    db[uid] = {"name": api_data['name'], "login": api_data['login'], "is_follower": True, "followed_at": api_data['followed_at'], "unfollowed_at": ""}
                    updated_count += 1
                else:
                    ud = db[uid]
                    if not ud.get("is_follower") or ud.get("followed_at") != api_data['followed_at'] or ud.get("unfollowed_at"):
                        ud["is_follower"] = True; ud["followed_at"] = api_data['followed_at']; ud["unfollowed_at"] = ""
                        updated_count += 1
            for uid, user_data in db.items():
                if user_data.get("is_follower") and uid not in all_followers_map:
                    user_data["is_follower"] = False
                    if not user_data.get("unfollowed_at"): user_data["unfollowed_at"] = today_str
                    updated_count += 1
            if updated_count > 0: c.save_viewers(db)

        return f"同期完了: {len(all_followers_map)}人確認、{updated_count}件更新。"
    except Exception as e: return f"エラー: {e}"

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
                if not sid: continue
                
                # 新規データ、または強制更新モードならデータを構築
                if sid not in idx or force_update:
                    old_data = idx.get(sid, {})
                    old_status = old_data.get("vod_status", "not_downloaded")
                    old_path = old_data.get("file_path")
                    old_log_count = old_data.get("log_count", 0)
                    old_max = old_data.get("max_viewers", 0)
                    old_avg_sum = old_data.get("avg_viewers_sum", 0)
                    old_source = old_data.get("source", "api")
                    # ★追加: 既存のフォロワー数をバックアップ
                    old_follower_count = old_data.get("follower_count", 0)
                    
                    # 既存のゲーム名があれば維持する
                    current_game_name = old_data.get("game_name", "Unknown")
                    new_game_name = current_game_name if current_game_name != "Unknown" else "Unknown"

                    idx[sid] = {
                        "start_time": v['created_at'], 
                        "title": v['title'],
                        "game_name": new_game_name,
                        "max_viewers": old_max,
                        "avg_viewers_sum": old_avg_sum,
                        "log_count": old_log_count,
                        "avg_viewers": round(old_avg_sum / old_log_count, 1) if old_log_count > 0 else 0,
                        # ★追加: ここで書き戻す
                        "follower_count": old_follower_count,
                        "source": old_source,
                        "vod_status": old_status,
                        "file_path": old_path,
                        "vod_id": v['id'],
                        "thumbnail_url": v['thumbnail_url'],
                        "duration": v['duration'],
                        "view_count": v['view_count']
                    }
                    if old_path: idx[sid]["file_path"] = old_path
                    
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
                
    except Exception as e: c.log(f"⚠️ 履歴同期エラー: {e}")

def flush_logs(conf, stream_data, chatters):
    global current_minute_stats
    ensure_directories()
    stream_id = stream_data['id']
    filename = f"data/history/stream_{stream_id}.jsonl"
    census = []
    
    db = c.load_viewers()
    for u in chatters:
        uid = u['user_id']; ud = db.get(uid, {})
        census.append({"id": uid, "name": u['user_name'], "is_sub": ud.get("is_sub", False), "is_follower": ud.get("is_follower", False)})

    with stats_lock:
        snapshot = {
            # ★変更: JSTのISOフォーマット
            "timestamp": c.get_now().isoformat(),
            "stream_info": { "title": stream_data.get('title'), "game": stream_data.get('game_name'), "tags": stream_data.get('tags', []), "follower_total": current_minute_stats.get("follower_total", 0) },
            "metrics": { "viewer_count": stream_data.get('viewer_count', 0), "chat_count": len(chatters), "msg_speed": len(current_minute_stats["messages"]), "bits": current_minute_stats["bits"], "gift_subs": current_minute_stats["gift_subs"] },
            "emotes": current_minute_stats["emote_counts"].copy(), "subs": current_minute_stats["subs"].copy(), "raids": current_minute_stats["raids"][:], "points": current_minute_stats["point_redemptions"][:], "badges": current_minute_stats["badges"].copy(), "messages": current_minute_stats["messages"][:], "census": census
        }
        fc = current_minute_stats["follower_total"]
        current_minute_stats = {
            "messages": [], "emote_counts": {}, "subs": {"Prime":0, "Tier1":0, "Tier2":0, "Tier3":0},
            "gift_subs": 0, "bits": 0, "raids": [], "point_redemptions": [], "badges": {},
            "follower_total": fc, "last_irc_activity": current_minute_stats["last_irc_activity"]
        }

    with open(filename, 'a', encoding='utf-8') as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    idx = load_stream_index()
    if stream_id not in idx:
        idx[stream_id] = {
            "start_time": stream_data.get("started_at"), "title": stream_data.get("title"), "game_name": stream_data.get("game_name"),
            "max_viewers": 0, "avg_viewers_sum": 0, "log_count": 0, "vod_status": "not_downloaded", "source": "bot"
        }
    
    item = idx[stream_id]
    item["max_viewers"] = max(item.get("max_viewers", 0), stream_data.get('viewer_count', 0))
    item["avg_viewers_sum"] = item.get("avg_viewers_sum", 0) + stream_data.get('viewer_count', 0)
    item["log_count"] = item.get("log_count", 0) + 1
    item["source"] = "bot"
    item["avg_viewers"] = round(item["avg_viewers_sum"] / item["log_count"], 1)
    item["title"] = stream_data.get("title")
    item["game_name"] = stream_data.get("game_name")
    item["follower_count"] = current_minute_stats.get("follower_total", 0)
    save_stream_index(idx)

# --- 動画ダウンロード処理 ---
def execute_download(conf, stream_id, idx_data):
    if not YT_DLP_AVAILABLE: return "yt-dlp not installed"
    
    if stream_id in cancel_requests:
        cancel_requests.discard(stream_id)

    if idx_data.get(stream_id, {}).get("vod_status") == "downloaded":
        c.log(f"ℹ️ {stream_id} は既にダウンロード済みです。")
        return "Already downloaded"

    c.log(f"🔎 アーカイブ検索開始 (StreamID: {stream_id})...")
    video_url = None; video_id = None
    
    try:
        if "vod_id" in idx_data.get(stream_id, {}):
            video_id = idx_data[stream_id]["vod_id"]
            video_url = f"https://www.twitch.tv/videos/{video_id}"
        else:
            url = f"https://api.twitch.tv/helix/videos?user_id={conf['broadcaster_id']}&type=archive&first=20"
            r = requests.get(url, headers=get_headers(conf), timeout=10)
            if r.status_code == 200:
                videos = r.json().get('data', [])
                st_str = idx_data[stream_id].get('start_time')
                for v in videos:
                    if v.get('stream_id') == str(stream_id):
                        video_url = v['url']; video_id = v['id']; break
                    if st_str:
                        st_dt = datetime.fromisoformat(st_str.replace('Z', '+00:00'))
                        v_dt = datetime.fromisoformat(v['created_at'].replace('Z', '+00:00'))
                        if abs((v_dt - st_dt).total_seconds()) < 3600:
                            video_url = v['url']; video_id = v['id']; break
        
        if not video_url:
            c.log(f"⚠️ アーカイブが見つかりませんでした (StreamID: {stream_id})")
            return "Video not found"

        idx_data[stream_id]["vod_id"] = video_id
        
        global download_progress
        download_progress[stream_id] = {"percent": 0, "speed": "Init...", "status": "starting"}

        idx_data[stream_id]["vod_status"] = "downloading"
        save_stream_index(idx_data)

        save_path = DOWNLOAD_DIR
        if not os.path.exists(save_path): os.makedirs(save_path)
        
        def progress_hook(d):
            if stream_id in cancel_requests:
                raise Exception("Download Cancelled by User")
            if d['status'] == 'downloading':
                try:
                    p = d.get('_percent_str', '0%').replace('%','')
                    s = d.get('_speed_str', '0B/s')
                    download_progress[stream_id] = {"percent": float(p), "speed": s, "status": "downloading"}
                except: pass
            elif d['status'] == 'finished':
                download_progress[stream_id] = {"percent": 100, "speed": "Done", "status": "finished"}

        file_date = "00000000"
        file_title = "Untitled"
        
        if stream_id in idx_data:
            meta = idx_data[stream_id]
            if "start_time" in meta and meta["start_time"]:
                try:
                    # ★変更: UTC(Z) -> JST に変換してファイル名の日付とする
                    dt_obj = datetime.fromisoformat(meta["start_time"].replace('Z', '+00:00')).astimezone(c.JST)
                    file_date = dt_obj.strftime('%Y%m%d')
                except:
                    pass
            if "title" in meta and meta["title"]:
                raw_title = meta["title"]
                file_title = re.sub(r'[\\/:*?"<>|]', '_', raw_title)

        out_template = f"{save_path}/{file_date}_{file_title}.%(ext)s"

        ydl_opts = {
            'outtmpl': out_template,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'quiet': True, 'no_warnings': True,
            'progress_hooks': [progress_hook]
        }

        c.log(f"⬇️ ダウンロード開始: {video_id} -> {file_date}_{file_title}.mp4")
        
        final_filename = None
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            final_filename = ydl.prepare_filename(info)
        
        idx = load_stream_index()
        if stream_id in idx:
            idx[stream_id]["vod_status"] = "downloaded"
            idx[stream_id]["vod_id"] = video_id
            idx[stream_id]["file_path"] = final_filename
            save_stream_index(idx)
            
        c.log(f"✅ ダウンロード完了: {final_filename}")
        if stream_id in download_progress: del download_progress[stream_id]
        return "Success"

    except Exception as e:
        if "Cancelled" in str(e):
            c.log(f"🛑 ダウンロードを中止しました: {stream_id}")
            idx = load_stream_index()
            if stream_id in idx: idx[stream_id]["vod_status"] = "not_downloaded"
            save_stream_index(idx)
            if stream_id in download_progress: del download_progress[stream_id]
            cleanup_temp_files(save_path, f"{file_date}_{file_title}")
            return "Cancelled"
        else:
            c.log(f"❌ ダウンロード失敗: {e}")
            idx = load_stream_index()
            if stream_id in idx: idx[stream_id]["vod_status"] = "failed"
            save_stream_index(idx)
            if stream_id in download_progress: download_progress[stream_id] = {"percent": 0, "speed": "Error", "status": "failed"}
            cleanup_temp_files(save_path, f"{file_date}_{file_title}")
            return f"Failed: {e}"

def request_cancel_download(stream_id):
    c.log(f"⚠️ ダウンロード中止リクエスト: {stream_id}")
    cancel_requests.add(stream_id)
    idx = load_stream_index()
    if stream_id in idx:
        if idx[stream_id].get("vod_status") == "downloading":
            idx[stream_id]["vod_status"] = "not_downloaded"
            save_stream_index(idx)
            global download_progress
            if stream_id in download_progress:
                del download_progress[stream_id]
            c.log(f"ℹ️ {stream_id} のステータスを強制リセットしました。")

def delete_vod_file(stream_id):
    idx = load_stream_index()
    if stream_id in idx:
        path = idx[stream_id].get("file_path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
                c.log(f"🗑️ ファイルを削除しました: {path}")
            except Exception as e:
                c.log(f"⚠️ ファイル削除失敗 (手動確認してください): {path} - {e}")
        else:
            c.log(f"ℹ️ 削除対象のファイルが見つかりませんでしたが、ステータスをリセットします。")
        idx[stream_id]["vod_status"] = "not_downloaded"
        if "file_path" in idx[stream_id]: del idx[stream_id]["file_path"]
        save_stream_index(idx)
        return True
    return False

def auto_download_task(conf, stream_id):
    c.log("⏳ 配信終了検知: 5分後にアーカイブ検索を開始します...")
    time.sleep(300) 
    idx = load_stream_index()
    execute_download(conf, stream_id, idx)

def bulk_download_task(conf):
    idx = load_stream_index()
    c.log("📦 未ダウンロードのアーカイブを一括処理します...")
    sorted_ids = sorted(idx.keys(), key=lambda k: idx[k].get('start_time', ''), reverse=True)
    count = 0
    for sid in sorted_ids:
        status = idx[sid].get("vod_status")
        if status != "downloaded" and status != "downloading":
            res = execute_download(conf, sid, idx)
            if res == "Success": count += 1
            if sid in cancel_requests: break 
            time.sleep(5)
    c.log(f"📦 一括処理完了: {count}件")

def get_debug_status():
    with stats_lock:
        return {
            "irc_connected": (time.time() - current_minute_stats["last_irc_activity"]) < 300,
            "last_irc_msg": datetime.fromtimestamp(current_minute_stats["last_irc_activity"]).strftime('%H:%M:%S') if current_minute_stats["last_irc_activity"] else "なし",
            "buffered_messages": len(current_minute_stats["messages"]),
            "current_follower_count": current_minute_stats["follower_total"],
            "log_path_exists": os.path.exists('data/history'),
            "yt_dlp_installed": YT_DLP_AVAILABLE,
            "download_dir": DOWNLOAD_DIR
        }

def get_download_progress():
    return download_progress

# --- Workers ---
def viewer_worker_loop(conf):
    c.log("Viewer/Monitor Worker Started")
    fix_dangling_states()
    
    last_log_time = time.time()
    last_follow_check = 0
    ensure_directories()
    try:
        if conf.get('broadcaster_id'): sync_vod_history(conf)
    except: pass
    
    time.sleep(5)
    while True:
        try:
            conf = c.load_config()
            
            if conf.get("is_running") and (time.time() - last_follow_check > 1800):
                force_update_followers(conf)
                sync_vod_history(conf)
                last_follow_check = time.time()

            if not conf.get("is_running"):
                time.sleep(10); continue
            
            if time.time() - last_log_time >= 55:
                ft = get_total_followers(conf)
                if ft > 0:
                    with stats_lock: current_minute_stats["follower_total"] = ft

            is_live, stream_data = check_stream_status_and_update(conf)
            stream_id = stream_data['id'] if is_live and stream_data else None

            if conf.get("ignore_stream_status") and not is_live:
                is_live = True; stream_id = "debug_stream"
                # ★変更: デバッグストリームの開始時間もJSTへ
                stream_data = {"id": "debug_stream", "title": "Debug", "game_name": "Debug", "viewer_count": 0, "started_at": c.get_now().isoformat()}

            if not is_live:
                if c.current_stream_id is not None:
                    finished_id = c.current_stream_id
                    c.log("⚫ 配信終了検知")
                    
                    idx = load_stream_index()
                    if finished_id in idx:
                        entry = idx[finished_id]
                        st_str = entry.get('start_time')
                        if st_str:
                            try:
                                start_dt = datetime.fromisoformat(st_str.replace('Z', '+00:00'))
                                # ★変更: 終了時間判定にもJST考慮 (UTC同士で比較するので .now(timezone.utc) でもよいが統一)
                                end_dt = datetime.now(timezone.utc) 
                                duration_sec = (end_dt - start_dt).total_seconds()
                                entry['duration'] = get_formatted_duration(duration_sec)
                                save_stream_index(idx)
                            except: pass

                    if conf.get('enable_vod_download'):
                        threading.Thread(target=auto_download_task, args=(conf, finished_id)).start()

                    c.current_session_viewers.clear()
                    c.current_stream_id = None
                    c.current_game = None
                time.sleep(20); continue
            
            if c.current_stream_id != stream_id:
                c.current_stream_id = stream_id
                c.current_game = stream_data.get('game_name')
                c.current_session_viewers.clear()
                c.state.reset()
                c.log(f"🟣 配信開始検知: {stream_data.get('title')} ({c.current_game})")
                
                idx = load_stream_index()
                if stream_id not in idx:
                    idx[stream_id] = {
                        "start_time": stream_data.get('started_at'),
                        "title": stream_data.get('title'),
                        "game_name": stream_data.get('game_name'),
                        "max_viewers": 0, "avg_viewers_sum": 0, "log_count": 0,
                        "source": "bot", "vod_status": "not_downloaded",
                        "thumbnail_url": stream_data.get('thumbnail_url', '').replace('{width}', '%{width}').replace('{height}', '%{height}')
                    }
                    save_stream_index(idx)
            else:
                c.current_game = stream_data.get('game_name')

            chatters = get_chatters(conf)
            if chatters is None: time.sleep(20); continue
            
            with c.file_lock:
                current_chatter_ids = set()
                db = c.load_viewers()
                now_ts = int(time.time())
                # ★変更: JSTの現在時刻オブジェクト
                now_dt = c.get_now()
                updated = False
                for u in chatters:
                    uid = u['user_id']
                    current_chatter_ids.add(uid)
                    if uid not in c.current_session_viewers: c.current_session_viewers[uid] = {"joined_at": now_dt, "name": u['user_name'], "login": u['user_login']}
                    if uid not in db: db[uid] = {"name": u['user_name'], "login": u['user_login'], "total_visits": 0, "streak": 0}
                    ud = db[uid]; ud["total_duration"] = ud.get("total_duration", 0) + 20
                    if ud.get("last_stream_id") != stream_id:
                        ud["total_visits"] = ud.get("total_visits", 0) + 1
                        ud["streak"] = ud.get("streak", 0) + 1 if ud.get("last_seen_ts", 0) > 0 and (now_ts - ud["last_seen_ts"]) < 129600 else 1
                        ud["last_stream_id"] = stream_id
                    ud["last_seen_ts"] = now_ts
                    updated = True
                for existing_uid in list(c.current_session_viewers.keys()):
                    if existing_uid not in current_chatter_ids: del c.current_session_viewers[existing_uid]
                if updated: c.save_viewers(db)
            
            if time.time() - last_log_time >= 60:
                flush_logs(conf, stream_data, chatters)
                last_log_time = time.time()

            time.sleep(20)
        except Exception as e: c.log(f"Viewer Err: {e}"); time.sleep(20)

def bot_worker():
    c.log("Bot Worker Started")
    while True:
        try:
            time.sleep(10); conf = c.load_config()
            if not conf.get("is_running"): continue
            if not c.current_stream_id and not conf.get("ignore_stream_status"): continue
            
            current_comments = c.state.get_count()
            # ★変更: JST現在時刻
            now = c.get_now()
            for i, rule in enumerate(conf.get("rules", [])):
                g = rule["game"]
                is_target = (g == "All") or (g == "Default" and not any(r["game"] == c.current_game for r in conf.get("rules", []) if r["game"] not in ["All", "Default"])) or (g == c.current_game)
                if not is_target: continue
                last = c.rule_last_executed.get(i, datetime.min.replace(tzinfo=c.JST)) # JST aware
                if (now - last) < timedelta(minutes=int(rule.get("interval", 15))): continue
                if (current_comments - c.rule_last_comment_count.get(i, 0)) < int(rule.get("min_comments", 2)): continue
                if send_chat(conf, rule["message"]):
                    c.log(f"Sent: {rule['message']}"); c.rule_last_executed[i] = now; c.rule_last_comment_count[i] = current_comments; time.sleep(1.0); break 
        except Exception as e: c.log(f"Bot Err: {e}"); time.sleep(60)

def irc_worker():
    while True:
        conf = c.load_config()
        if not conf.get("is_running") or not conf["access_token"]: time.sleep(10); continue
        s = socket.socket()
        try:
            s.settimeout(300); s.connect(('irc.chat.twitch.tv', 6667))
            token = conf['access_token'].replace("oauth:", "").strip(); nick = conf['channel_name'].lower().strip()
            s.send(f"CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership\n".encode())
            s.send(f"PASS oauth:{token}\nNICK {nick}\n".encode())
            joined=False; buf=""
            while True:
                try: ch = s.recv(4096).decode(errors='ignore')
                except: continue
                if not ch: break
                buf += ch
                while '\r\n' in buf:
                    l, buf = buf.split('\r\n', 1)
                    if not l: continue
                    if "001" in l and not joined: s.send(f"JOIN #{nick}\n".encode()); joined=True
                    if 'PING' in l: s.send("PONG\n".encode())
                    with stats_lock: current_minute_stats["last_irc_activity"] = time.time()
                    tags = {k: v for k, v in [item.split('=') for item in l.split(' ', 1)[0][1:].split(';') if '=' in item]} if l.startswith('@') else {}
                    if 'PRIVMSG' in l:
                        c.state.increment()
                        try:
                            parts = l.split('PRIVMSG', 1); msg_content = parts[1].split(':', 1)[1].strip() if len(parts) > 1 else ""
                            display_name = tags.get('display-name', tags.get('login', 'unknown')); is_sub = tags.get('subscriber') == '1'; bits = int(tags.get('bits', 0))
                            with stats_lock:
                                # ★変更: チャットログの時刻をJSTに
                                current_minute_stats["messages"].append({"time": c.get_now().strftime('%H:%M:%S'), "user": display_name, "text": msg_content, "is_sub": is_sub, "badges": tags.get('badges', '')})
                                if tags.get('emotes'):
                                    for grp in tags['emotes'].split('/'):
                                        eid = grp.split(':')[0]; count = len(grp.split(':')[1].split(','))
                                        current_minute_stats["emote_counts"][eid] = current_minute_stats["emote_counts"].get(eid, 0) + count
                                if bits > 0: current_minute_stats["bits"] += bits; c.log(f"💰 Cheer! {display_name}: {bits} bits")
                                if tags.get('custom-reward-id'): current_minute_stats["point_redemptions"].append({"user": display_name, "reward_id": tags.get('custom-reward-id'), "text": msg_content})
                                if tags.get('badges'):
                                    for b in tags['badges'].split(','): b_name = b.split('/')[0]; current_minute_stats["badges"][b_name] = current_minute_stats["badges"].get(b_name, 0) + 1
                            uid = tags.get('user-id')
                            if uid:
                                with c.file_lock:
                                    db = c.load_viewers()
                                    if uid not in db: db[uid] = {"name": display_name, "login": display_name.lower(), "total_visits": 1}
                                    ud = db[uid]; ud["total_comments"] = ud.get("total_comments", 0) + 1; ud["total_bits"] = ud.get("total_bits", 0) + bits; ud["is_sub"] = is_sub; ud["last_seen_ts"] = int(time.time()); c.save_viewers(db)
                        except: pass
                    elif 'USERNOTICE' in l:
                        try:
                            msg_id = tags.get('msg-id'); display_name = tags.get('display-name', 'Anonymous')
                            with stats_lock:
                                if msg_id in ['sub', 'resub']:
                                    plan = tags.get('msg-param-sub-plan', 'Tier1'); plan_name = "Prime" if plan == "Prime" else ("Tier2" if plan == "2000" else ("Tier3" if plan == "3000" else "Tier1"))
                                    current_minute_stats["subs"][plan_name] += 1; c.log(f"🎉 Sub! {display_name} ({plan_name})")
                                elif msg_id == 'subgift': current_minute_stats["gift_subs"] += 1; c.log(f"🎁 Gift Sub! {display_name}")
                                elif msg_id == 'raid': viewer_count = int(tags.get('msg-param-viewerCount', 0)); current_minute_stats["raids"].append({"user": display_name, "count": viewer_count}); c.log(f"🚨 Raid! {display_name} ({viewer_count} viewers)")
                        except: pass
        except Exception as e: c.log(f"IRC Err: {e}"); time.sleep(10)
        finally: 
            try: s.close()
            except: pass; time.sleep(10)

def create_prediction(conf, title, outcomes, duration=120):
    """
    予想を作成する
    outcomes: ["選択肢1", "選択肢2"] のリスト
    duration: 受付時間(秒)
    """
    url = "https://api.twitch.tv/helix/predictions"
    headers = get_broadcaster_headers(conf)
    
    # APIの仕様に合わせて選択肢データを整形
    outcome_objects = [{"title": o} for o in outcomes]
    
    payload = {
        "broadcaster_id": conf['broadcaster_id'],
        "title": title,
        "outcomes": outcome_objects,
        "prediction_window": duration
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()['data'][0]
            c.log(f"🎰 予想を開始しました: {title}")
            return True, data
        else:
            c.log(f"⚠️ 予想作成失敗: {r.text}")
            return False, r.text
    except Exception as e:
        return False, str(e)

def resolve_prediction(conf, prediction_id, winning_outcome_id):
    """
    予想を終了し、結果を確定する
    """
    url = "https://api.twitch.tv/helix/predictions"
    headers = get_broadcaster_headers(conf)
    
    payload = {
        "broadcaster_id": conf['broadcaster_id'],
        "id": prediction_id,
        "status": "RESOLVED",
        "winning_outcome_id": winning_outcome_id
    }
    
    try:
        r = requests.patch(url, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            c.log(f"✅ 予想を終了しました (勝者確定)")
            return True, r.json()['data'][0]
        else:
            c.log(f"⚠️ 予想終了失敗: {r.text}")
            return False, r.text
    except Exception as e:
        return False, str(e)

def cancel_prediction(conf, prediction_id):
    """
    予想をキャンセル（返金）する
    """
    url = "https://api.twitch.tv/helix/predictions"
    headers = get_broadcaster_headers(conf)
    
    payload = {
        "broadcaster_id": conf['broadcaster_id'],
        "id": prediction_id,
        "status": "CANCELED"
    }
    
    try:
        r = requests.patch(url, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            c.log(f"🚫 予想をキャンセルしました")
            return True, r.json()['data'][0]
        else:
            return False, r.text
    except Exception as e:
        return False, str(e)

# --- (既存の get_active_prediction 等があればそれも利用可能ですが、今回は新規作成を前提とします) ---
def get_current_prediction(conf):
    """現在進行中の予想を取得"""
    url = f"https://api.twitch.tv/helix/predictions?broadcaster_id={conf['broadcaster_id']}"
    headers = get_broadcaster_headers(conf)
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json().get('data', [])
            # ACTIVE または LOCKED (集計待ち) のものを返す
            for p in data:
                if p['status'] in ['ACTIVE', 'LOCKED']:
                    return p
    except: pass
    return None


# ★追加: 指定された配信IDの全データ(履歴、ログ、録画)を削除する関数
def delete_history_data(stream_id):
    c.log(f"🗑️ 履歴削除プロセス開始: {stream_id}")
    
    # 1. インデックス(json)と録画ファイルの削除
    with c.file_lock:
        idx = load_stream_index()
        if stream_id in idx:
            # 録画ファイルがあれば削除
            file_path = idx[stream_id].get("file_path")
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    c.log(f"🗑️ ファイル削除: {file_path}")
                except Exception as e:
                    c.log(f"⚠️ ファイル削除失敗: {e}")
            
            # インデックスから削除
            del idx[stream_id]
            save_stream_index(idx)
            c.log("✅ インデックス情報を削除しました")
        else:
            c.log("ℹ️ インデックス情報は見つかりませんでした")

    # 2. ログファイル(.jsonl)の削除
    log_path = f"data/history/stream_{stream_id}.jsonl"
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
            c.log(f"🗑️ ログファイル削除: {log_path}")
        except Exception as e:
            c.log(f"⚠️ ログ削除失敗: {e}")
    
    return True


def start_workers():
    threading.Thread(target=bot_worker, daemon=True).start()
    threading.Thread(target=irc_worker, daemon=True).start()
    threading.Thread(target=viewer_worker_loop, args=(c.load_config(),), daemon=True).start()