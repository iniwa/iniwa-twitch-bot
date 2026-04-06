import time
import socket
import ssl
import threading
import config as c

PLAN_MAP = {
    'Prime': 'Prime',
    '2000': 'Tier2',
    '3000': 'Tier3',
}


def resolve_plan_name(plan_code):
    return PLAN_MAP.get(plan_code, 'Tier1')


def parse_tags(line):
    """IRC タグを解析して辞書で返す"""
    tags = {}
    if line.startswith('@'):
        tag_str = line.split(' ', 1)[0][1:]
        for item in tag_str.split(';'):
            if '=' in item:
                k, v = item.split('=', 1)
                tags[k] = v
    return tags


def handle_privmsg(line, tags, nick, sock, conf, stats_lock, current_minute_stats):
    """PRIVMSG (チャットメッセージ) の処理"""
    c.state.increment()

    parts = line.split('PRIVMSG', 1)
    msg_content = parts[1].split(':', 1)[1].strip() if len(parts) > 1 else ''
    display_name = tags.get('display-name', tags.get('login', 'unknown'))
    is_sub = tags.get('subscriber') == '1'
    bits = int(tags.get('bits', 0))

    with stats_lock:
        current_minute_stats['messages'].append({
            'time': c.get_now().strftime('%H:%M:%S'),
            'user': display_name,
            'text': msg_content,
            'is_sub': is_sub,
            'badges': tags.get('badges', '')
        })

        # エモート集計
        if tags.get('emotes'):
            for grp in tags['emotes'].split('/'):
                eid = grp.split(':')[0]
                count = len(grp.split(':')[1].split(','))
                current_minute_stats['emote_counts'][eid] = (
                    current_minute_stats['emote_counts'].get(eid, 0) + count
                )

        # ビッツ
        if bits > 0:
            current_minute_stats['bits'] += bits
            current_minute_stats['events'].append({
                'type': 'bits', 'user': display_name,
                'user_id': tags.get('user-id'),
                'amount': bits
            })
            c.log(f'💰 Cheer! {display_name}: {bits} bits')
            c.log_event({'type': 'bits', 'user': display_name, 'amount': bits})

        # チャンネルポイント
        if tags.get('custom-reward-id'):
            current_minute_stats['point_redemptions'].append({
                'user': display_name,
                'reward_id': tags.get('custom-reward-id'),
                'text': msg_content
            })

        # バッジ集計
        if tags.get('badges'):
            for badge in tags['badges'].split(','):
                badge_name = badge.split('/')[0]
                current_minute_stats['badges'][badge_name] = (
                    current_minute_stats['badges'].get(badge_name, 0) + 1
                )

    # 視聴者DB更新
    uid = tags.get('user-id')
    if uid:
        with c.file_lock:
            db = c.load_viewers()
            is_new_user = uid not in db
            if is_new_user:
                db[uid] = {
                    'name': display_name,
                    'login': display_name.lower(),
                    'total_visits': 1
                }
            ud = db[uid]
            ud['total_comments'] = ud.get('total_comments', 0) + 1
            ud['total_bits'] = ud.get('total_bits', 0) + bits
            ud['is_sub'] = is_sub
            ud['last_seen_ts'] = int(time.time())
            c.save_viewers(db)

        if is_new_user and conf.get('enable_welcome') and c.current_stream_id:
            welcome = f'@{display_name} ようこそ！初コメありがとう！'
            sock.send(f'PRIVMSG #{nick} :{welcome}\r\n'.encode())


def handle_usernotice(tags, stats_lock, current_minute_stats):
    """USERNOTICE (サブスク, ギフト, レイド等) の処理"""
    msg_id = tags.get('msg-id')
    display_name = tags.get('display-name', 'Anonymous')
    uid = tags.get('user-id')

    with stats_lock:
        if msg_id in ('sub', 'resub'):
            plan_name = resolve_plan_name(tags.get('msg-param-sub-plan', 'Tier1'))
            months = int(tags.get('msg-param-cumulative-months', 1))
            current_minute_stats['subs'][plan_name] += 1
            current_minute_stats['events'].append({
                'type': 'sub', 'user': display_name,
                'user_id': uid, 'plan': plan_name,
                'months': months, 'msg_id': msg_id
            })
            c.log(f'🎉 Sub! {display_name} ({plan_name}, {months}ヶ月)')
            c.log_event({
                'type': 'sub', 'user': display_name,
                'plan': plan_name, 'months': months,
                'is_resub': msg_id == 'resub'
            })

        elif msg_id == 'subgift':
            plan_name = resolve_plan_name(tags.get('msg-param-sub-plan', 'Tier1'))
            recipient = tags.get('msg-param-recipient-display-name', '?')
            recipient_id = tags.get('msg-param-recipient-id')
            current_minute_stats['gift_subs'] += 1
            current_minute_stats['events'].append({
                'type': 'subgift', 'user': display_name,
                'user_id': uid, 'recipient': recipient,
                'recipient_id': recipient_id,
                'plan': plan_name
            })
            c.log(f'🎁 Gift Sub! {display_name} → {recipient} ({plan_name})')
            c.log_event({
                'type': 'subgift', 'user': display_name,
                'recipient': recipient, 'plan': plan_name
            })

        elif msg_id == 'submysterygift':
            gift_count = int(tags.get('msg-param-mass-gift-count', 1))
            current_minute_stats['events'].append({
                'type': 'submysterygift', 'user': display_name,
                'user_id': uid, 'count': gift_count
            })
            c.log(f'🎁 Mystery Gift! {display_name} ({gift_count}人分)')
            c.log_event({
                'type': 'submysterygift', 'user': display_name,
                'count': gift_count
            })

        elif msg_id == 'raid':
            viewer_count = int(tags.get('msg-param-viewerCount', 0))
            current_minute_stats['raids'].append({
                'user': display_name, 'count': viewer_count
            })
            current_minute_stats['events'].append({
                'type': 'raid', 'user': display_name,
                'user_id': uid, 'count': viewer_count
            })
            c.log(f'🚨 Raid! {display_name} ({viewer_count} viewers)')
            c.log_event({
                'type': 'raid', 'user': display_name,
                'count': viewer_count
            })

    # 視聴者DBにサブスク/ギフト履歴を反映
    if uid and msg_id in ('sub', 'resub', 'subgift', 'submysterygift'):
        with c.file_lock:
            db = c.load_viewers()
            if uid not in db:
                db[uid] = {
                    'name': display_name,
                    'login': display_name.lower(),
                    'total_visits': 0
                }
            ud = db[uid]
            if msg_id in ('sub', 'resub'):
                plan_name = resolve_plan_name(tags.get('msg-param-sub-plan', 'Tier1'))
                ud['total_sub_months'] = ud.get('total_sub_months', 0) + 1
                ud['last_sub_ts'] = int(time.time())
                ud['last_sub_plan'] = plan_name
            elif msg_id in ('subgift', 'submysterygift'):
                gift_count = int(tags.get('msg-param-mass-gift-count', 1)) if msg_id == 'submysterygift' else 1
                ud['total_gifts_given'] = ud.get('total_gifts_given', 0) + gift_count

            # ギフトを受け取った側も記録
            if msg_id == 'subgift':
                recipient_id = tags.get('msg-param-recipient-id')
                recipient = tags.get('msg-param-recipient-display-name', '?')
                if recipient_id:
                    if recipient_id not in db:
                        db[recipient_id] = {
                            'name': recipient,
                            'login': recipient.lower(),
                            'total_visits': 0
                        }
                    db[recipient_id]['total_gifts_received'] = (
                        db[recipient_id].get('total_gifts_received', 0) + 1
                    )
            c.save_viewers(db)


def irc_worker(stats_lock, current_minute_stats):
    while True:
        conf = c.load_config()
        if not conf.get('is_running') or not conf['access_token']:
            time.sleep(10)
            continue

        raw_sock = socket.socket()
        sock = None
        try:
            raw_sock.settimeout(300)
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw_sock, server_hostname='irc.chat.twitch.tv')
            sock.connect(('irc.chat.twitch.tv', 6697))

            token = conf['access_token'].replace('oauth:', '').strip()
            nick = conf['channel_name'].lower().strip()
            sock.send(f'CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership\r\n'.encode())
            sock.send(f'PASS oauth:{token}\r\nNICK {nick}\r\n'.encode())
            joined = False
            buf = ''

            while True:
                try:
                    chunk = sock.recv(4096).decode(errors='ignore')
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk

                while '\r\n' in buf:
                    line, buf = buf.split('\r\n', 1)
                    if not line:
                        continue
                    if '001' in line and not joined:
                        sock.send(f'JOIN #{nick}\r\n'.encode())
                        joined = True
                    if 'PING' in line:
                        sock.send('PONG\r\n'.encode())

                    with stats_lock:
                        current_minute_stats['last_irc_activity'] = time.time()

                    tags = parse_tags(line)

                    try:
                        if 'PRIVMSG' in line:
                            handle_privmsg(line, tags, nick, sock, conf, stats_lock, current_minute_stats)
                        elif 'USERNOTICE' in line:
                            handle_usernotice(tags, stats_lock, current_minute_stats)
                    except (ValueError, IndexError, KeyError) as e:
                        c.log(f'IRC parse error: {e}')

        except Exception as e:
            c.log(f'IRC Err: {e}')
        finally:
            try:
                if sock:
                    sock.close()
                else:
                    raw_sock.close()
            except OSError:
                pass
            time.sleep(10)
