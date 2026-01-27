import os
import json
import threading
from datetime import datetime, timedelta, timezone

CONFIG_FILE = 'data/config.json'
VIEWERS_FILE = 'data/viewers.json'

logs = []
MAX_LOGS = 50

rule_last_executed = {}
rule_last_comment_count = {} 
current_session_viewers = {}
current_stream_id = None
current_game = None

# JST (日本標準時) の定義
JST = timezone(timedelta(hours=9))

file_lock = threading.RLock()

class ThreadSafeCounter:
    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()
    def increment(self):
        with self._lock: self._count += 1
    def get_count(self):
        with self._lock: return self._count
    def reset(self):
        with self._lock: self._count = 0

state = ThreadSafeCounter()

def log(message):
    global logs
    print(message)
    logs.insert(0, message)
    if len(logs) > MAX_LOGS: logs.pop()

# 現在時刻をJSTで取得する共通関数
def get_now():
    return datetime.now(JST)

def save_config(conf):
    with file_lock:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(conf, f, indent=4, ensure_ascii=False)

def load_viewers():
    if not os.path.exists(VIEWERS_FILE): return {}
    try:
        with file_lock:
            with open(VIEWERS_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_viewers(data):
    with file_lock:
        with open(VIEWERS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

# 重複を削除し、最新の定義のみを残しました
def load_config():
    if not os.path.exists('data'): os.makedirs('data')
    # デフォルト設定
    default_conf = {
        "client_id": "", "access_token": "", "broadcaster_id": "", "bot_user_id": "", "channel_name": "", 
        "is_running": False, "rules": [], "presets": [],
        "prediction_presets": [],
        "layout": {
            "columns": 2,
            "max_width": 1400,
            "cards": {
                # order: 表示順 (小さいほど先頭)
                "viewers": {"span": 1, "height": 400, "order": 1},
                "presets": {"span": 1, "height": 400, "order": 2},
                "prediction": {"span": 1, "height": 400, "order": 3},
                "rules": {"span": 2, "height": 0, "order": 4},
                "logs": {"span": 2, "height": 200, "order": 5}
            }
        }
    }

    if not os.path.exists(CONFIG_FILE):
        return default_conf
    try:
        with file_lock:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                
                # --- 設定ファイルの補完ロジック (強化版) ---
                # 1. トップレベルのキー不足を補完
                for k, v in default_conf.items():
                    if k not in saved: saved[k] = v
                
                # 2. layoutキーの補完
                if "layout" not in saved: saved["layout"] = default_conf["layout"]
                else:
                    # 3. layout内の各カード設定の補完
                    for ck, cv in default_conf["layout"]["cards"].items():
                        if ck not in saved["layout"]["cards"]:
                            saved["layout"]["cards"][ck] = cv
                        else:
                            # 4. カード内の詳細キー(order等)の不足を補完
                            for field, val in cv.items():
                                if field not in saved["layout"]["cards"][ck]:
                                    saved["layout"]["cards"][ck][field] = val
                                    
                return saved
    except: return default_conf