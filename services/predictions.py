import requests
import config as c
from services.twitch_api import get_broadcaster_headers


def create_prediction(conf, title, outcomes, duration=120):
    url = "https://api.twitch.tv/helix/predictions"
    headers = get_broadcaster_headers(conf)

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


def get_current_prediction(conf):
    url = f"https://api.twitch.tv/helix/predictions?broadcaster_id={conf['broadcaster_id']}"
    headers = get_broadcaster_headers(conf)
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json().get('data', [])
            for p in data:
                if p['status'] in ['ACTIVE', 'LOCKED']:
                    return p
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None
