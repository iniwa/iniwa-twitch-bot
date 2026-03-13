import os
import re
import time
import requests
from datetime import datetime
import config as c
from services.storage import (
    load_stream_index, save_stream_index,
    ARCHIVE_WAIT_DIR, _cleanup_temp_files
)
from services.twitch_api import get_headers

try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False


def get_download_progress():
    with c.download_lock:
        return dict(c.download_progress)


def request_cancel_download(stream_id):
    c.log(f"⚠️ ダウンロード中止リクエスト: {stream_id}")
    with c.download_lock:
        c.cancel_requests.add(stream_id)

    idx = load_stream_index()
    if stream_id in idx:
        if idx[stream_id].get("vod_status") == "downloading":
            idx[stream_id]["vod_status"] = "not_downloaded"
            save_stream_index(idx)
            with c.download_lock:
                c.download_progress.pop(stream_id, None)
            c.log(f"ℹ️ {stream_id} のステータスを強制リセットしました。")


def delete_vod_file(stream_id):
    idx = load_stream_index()
    if stream_id in idx:
        path = idx[stream_id].get("file_path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
                c.log(f"🗑️ ファイルを削除しました: {path}")
            except OSError as e:
                c.log(f"⚠️ ファイル削除失敗 (手動確認してください): {path} - {e}")
        else:
            c.log(f"ℹ️ 削除対象のファイルが見つかりませんでしたが、ステータスをリセットします。")
        idx[stream_id]["vod_status"] = "not_downloaded"
        idx[stream_id].pop("file_path", None)
        save_stream_index(idx)
        return True
    return False


def execute_download(conf, stream_id):
    if not YT_DLP_AVAILABLE:
        return "yt-dlp not installed"

    with c.download_lock:
        c.cancel_requests.discard(stream_id)

    idx_data = load_stream_index()

    if idx_data.get(stream_id, {}).get("vod_status") == "downloaded":
        c.log(f"ℹ️ {stream_id} は既にダウンロード済みです。")
        return "Already downloaded"

    c.log(f"🔎 アーカイブ検索開始 (StreamID: {stream_id})...")
    video_url = None
    video_id = None
    file_date = "00000000"
    file_title = "Untitled"
    save_path = ARCHIVE_WAIT_DIR

    try:
        if "vod_id" in idx_data.get(stream_id, {}):
            video_id = idx_data[stream_id]["vod_id"]
            video_url = f"https://www.twitch.tv/videos/{video_id}"
        else:
            url = f"https://api.twitch.tv/helix/videos?user_id={conf['broadcaster_id']}&type=archive&first=20"
            r = requests.get(url, headers=get_headers(conf), timeout=10)
            if r.status_code == 200:
                videos = r.json().get('data', [])
                st_str = idx_data.get(stream_id, {}).get('start_time')
                for v in videos:
                    if v.get('stream_id') == str(stream_id):
                        video_url = v['url']
                        video_id = v['id']
                        break
                    if st_str:
                        st_dt = datetime.fromisoformat(st_str.replace('Z', '+00:00'))
                        v_dt = datetime.fromisoformat(v['created_at'].replace('Z', '+00:00'))
                        if abs((v_dt - st_dt).total_seconds()) < 3600:
                            video_url = v['url']
                            video_id = v['id']
                            break

        if not video_url:
            c.log(f"⚠️ アーカイブが見つかりませんでした (StreamID: {stream_id})")
            return "Video not found"

        with c.file_lock:
            idx_data = load_stream_index()
            if stream_id not in idx_data:
                return "Stream ID missing in index"
            idx_data[stream_id]["vod_id"] = video_id
            idx_data[stream_id]["vod_status"] = "downloading"
            save_stream_index(idx_data)

        with c.download_lock:
            c.download_progress[stream_id] = {"percent": 0, "speed": "Init...", "status": "starting"}

        os.makedirs(save_path, exist_ok=True)

        def progress_hook(d):
            with c.download_lock:
                cancelled = stream_id in c.cancel_requests
            if cancelled:
                raise Exception("Download Cancelled by User")
            if d['status'] == 'downloading':
                try:
                    p = d.get('_percent_str', '0%').replace('%', '')
                    s = d.get('_speed_str', '0B/s')
                    with c.download_lock:
                        c.download_progress[stream_id] = {
                            "percent": float(p), "speed": s, "status": "downloading"
                        }
                except (ValueError, TypeError):
                    pass
            elif d['status'] == 'finished':
                with c.download_lock:
                    c.download_progress[stream_id] = {"percent": 100, "speed": "Done", "status": "finished"}

        meta = idx_data.get(stream_id, {})
        if "start_time" in meta and meta["start_time"]:
            try:
                dt_obj = datetime.fromisoformat(
                    meta["start_time"].replace('Z', '+00:00')
                ).astimezone(c.JST)
                file_date = dt_obj.strftime('%Y%m%d')
            except (ValueError, TypeError):
                pass
        if "title" in meta and meta["title"]:
            raw_title = meta["title"]
            file_title = re.sub(r'[\\/:*?"<>|%]', '_', raw_title)

        out_template = f"{save_path}/{file_date}_{file_title}.%(ext)s"

        ydl_opts = {
            'outtmpl': out_template,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'quiet': True, 'no_warnings': True,
            'progress_hooks': [progress_hook]
        }

        c.log(f"⬇️ ダウンロード開始: {video_id}")

        final_filename = None
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            final_filename = ydl.prepare_filename(info)

        with c.file_lock:
            idx = load_stream_index()
            if stream_id in idx:
                idx[stream_id]["vod_status"] = "downloaded"
                idx[stream_id]["vod_id"] = video_id
                idx[stream_id]["file_path"] = final_filename
                save_stream_index(idx)

        c.log(f"✅ ダウンロード完了: {final_filename}")
        with c.download_lock:
            c.download_progress.pop(stream_id, None)
        return "Success"

    except Exception as e:
        msg = str(e)
        status = "failed"
        if "Cancelled" in msg:
            c.log(f"🛑 ダウンロードを中止しました: {stream_id}")
            msg = "Cancelled"
            status = "not_downloaded"
        else:
            c.log(f"❌ ダウンロード失敗: {e}")
            with c.download_lock:
                if stream_id in c.download_progress:
                    c.download_progress[stream_id] = {"percent": 0, "speed": "Error", "status": "failed"}

        with c.file_lock:
            idx = load_stream_index()
            if stream_id in idx:
                idx[stream_id]["vod_status"] = status
                save_stream_index(idx)

        _cleanup_temp_files(save_path, f"{file_date}_{file_title}")
        return f"Result: {msg}"


def auto_download_task(conf, stream_id):
    c.log("⏳ 配信終了検知: 5分後にアーカイブ検索を開始します...")
    time.sleep(300)
    execute_download(conf, stream_id)


def bulk_download_task(conf):
    idx = load_stream_index()
    c.log("📦 未ダウンロードのアーカイブを一括処理します...")
    sorted_ids = sorted(idx.keys(), key=lambda k: idx[k].get('start_time', ''), reverse=True)
    count = 0
    for sid in sorted_ids:
        status = idx[sid].get("vod_status")
        if status != "downloaded" and status != "downloading":
            res = execute_download(conf, sid)
            if res == "Success":
                count += 1
            with c.download_lock:
                if sid in c.cancel_requests:
                    break
            time.sleep(5)
    c.log(f"📦 一括処理完了: {count}件")
