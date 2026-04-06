import os
import json
import threading
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
VIEWERS_FILE = os.path.join(DATA_DIR, 'viewers.json')

MAX_LOGS = 50
MAX_EVENTS = 200

# JST (日本標準時) の定義
JST = timezone(timedelta(hours=9))

# スレッド安全なリストとロック
logs = []
events = []
_log_lock = threading.Lock()

rule_last_executed = {}
rule_last_comment_count = {}
current_session_viewers = {}
current_stream_id = None
current_game = None

file_lock = threading.RLock()
download_lock = threading.Lock()

# ダウンロード状態 (download_lock で保護)
download_progress = {}
cancel_requests = set()


DEFAULT_LAYOUT = {
    'columns': 2,
    'max_width': 1400,
    'cards': {
        'viewers': {'span': 1, 'height': 400, 'order': 1},
        'presets': {'span': 1, 'height': 400, 'order': 2},
        'prediction': {'span': 1, 'height': 400, 'order': 3},
        'rules': {'span': 2, 'height': 0, 'order': 4},
        'logs': {'span': 2, 'height': 200, 'order': 5}
    }
}

DEFAULT_CONFIG = {
    'client_id': '', 'access_token': '', 'broadcaster_id': '',
    'bot_user_id': '', 'channel_name': '',
    'is_running': False, 'rules': [], 'presets': [],
    'prediction_presets': [],
    'layout': DEFAULT_LAYOUT
}


class ThreadSafeCounter:
    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()

    def increment(self):
        with self._lock:
            self._count += 1

    def get_count(self):
        with self._lock:
            return self._count

    def reset(self):
        with self._lock:
            self._count = 0


state = ThreadSafeCounter()


def log(message):
    print(message)
    with _log_lock:
        logs.insert(0, message)
        if len(logs) > MAX_LOGS:
            logs.pop()


def log_event(event):
    """イベントログに追加 (サブスク, ギフト, レイド, フォロー等)"""
    event['timestamp'] = get_now().isoformat()
    with _log_lock:
        events.insert(0, event)
        if len(events) > MAX_EVENTS:
            events.pop()


def get_now():
    return datetime.now(JST)


def parse_iso_jst(iso_str):
    """ISO 8601文字列をJST datetimeに変換"""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace('Z', '+00:00')).astimezone(JST)
    except (ValueError, TypeError):
        return None


def save_config(conf):
    with file_lock:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(conf, f, indent=4, ensure_ascii=False)


def load_viewers():
    if not os.path.exists(VIEWERS_FILE):
        return {}
    try:
        with file_lock:
            with open(VIEWERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_viewers(data):
    with file_lock:
        with open(VIEWERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)


def _deep_merge(base, override):
    """base の構造をデフォルトとして、override の値で上書きマージする"""
    merged = base.copy()
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_config():
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    try:
        with file_lock:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                return _deep_merge(DEFAULT_CONFIG, saved)
    except (json.JSONDecodeError, OSError):
        return DEFAULT_CONFIG.copy()
