import os
import json
import re
from datetime import datetime
import config as c

DOWNLOAD_DIR = '/app/downloads'
ARCHIVE_WAIT_DIR = '/app/downloads/wait'
ARCHIVE_ENCODE_DIR = '/app/downloads/encode'


def ensure_directories():
    os.makedirs('data/history', exist_ok=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_WAIT_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_ENCODE_DIR, exist_ok=True)


def load_stream_index():
    path = 'data/history/stream_index.json'
    if not os.path.exists(path):
        return {}
    with c.file_lock:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}


def save_stream_index(data):
    ensure_directories()
    with c.file_lock:
        with open('data/history/stream_index.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def get_formatted_duration(seconds):
    if not seconds:
        return '0m'
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f'{h}h{m}m{s}s'
    return f'{m}m{s}s'


def sanitize_filename(title):
    """ファイル名に使えない文字をアンダースコアに置換"""
    return re.sub(r'[\\/:*?"<>|%]', '_', title)


def get_file_date(start_time_str):
    """start_time 文字列から YYYYMMDD 形式の日付を取得"""
    if not start_time_str:
        return '00000000'
    try:
        dt_obj = datetime.fromisoformat(
            start_time_str.replace('Z', '+00:00')
        ).astimezone(c.JST)
        return dt_obj.strftime('%Y%m%d')
    except (ValueError, TypeError):
        return '00000000'


def cleanup_temp_files(save_path, filename_base):
    """ダウンロード中の一時ファイルを削除"""
    if not filename_base:
        return
    patterns = [
        f'{filename_base}.mp4.part',
        f'{filename_base}.mp4.ytdl',
        f'{filename_base}.part',
        f'{filename_base}.ytdl'
    ]
    for pat in patterns:
        full_path = os.path.join(save_path, pat)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
                c.log(f'🧹 ゴミファイルを削除しました: {pat}')
            except OSError as e:
                c.log(f'⚠️ ゴミ削除失敗: {pat} ({e})')


def fix_dangling_states():
    c.log('🔧 起動時チェック: ダウンロード状態の整合性を確認中...')
    with c.file_lock:
        idx = load_stream_index()
        modified = False

        for sid, data in idx.items():
            if data.get('vod_status') != 'downloading':
                continue

            file_date = get_file_date(data.get('start_time'))
            file_title = sanitize_filename(data.get('title', 'Untitled'))
            expected_filename = f'{file_date}_{file_title}.mp4'
            full_path = os.path.join(ARCHIVE_WAIT_DIR, expected_filename)

            if os.path.exists(full_path):
                c.log(f'✅ 復旧: {expected_filename} を確認。ステータスを[保存済]に修正します。')
                data['vod_status'] = 'downloaded'
                data['file_path'] = full_path
                if 'vod_id' not in data:
                    data['vod_id'] = sid
                modified = True
            else:
                c.log(f'⚠️ リセット: {sid} の完了ファイルがありません。ステータスを戻し、ゴミを掃除します。')
                cleanup_temp_files(ARCHIVE_WAIT_DIR, f'{file_date}_{file_title}')
                data['vod_status'] = 'not_downloaded'
                modified = True

        if modified:
            save_stream_index(idx)


def delete_history_data(stream_id):
    c.log(f'🗑️ 履歴削除プロセス開始: {stream_id}')

    with c.file_lock:
        idx = load_stream_index()
        if stream_id in idx:
            file_path = idx[stream_id].get('file_path')
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    c.log(f'🗑️ ファイル削除: {file_path}')
                except OSError as e:
                    c.log(f'⚠️ ファイル削除失敗: {e}')

            del idx[stream_id]
            save_stream_index(idx)
            c.log('✅ インデックス情報を削除しました')
        else:
            c.log('ℹ️ インデックス情報は見つかりませんでした')

    log_path = f'data/history/stream_{stream_id}.jsonl'
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
            c.log(f'🗑️ ログファイル削除: {log_path}')
        except OSError as e:
            c.log(f'⚠️ ログ削除失敗: {e}')

    return True
