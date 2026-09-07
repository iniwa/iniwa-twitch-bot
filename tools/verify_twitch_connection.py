"""Explicit read-only credential check. Prints capability flags, never identities.

The caller supplies one existing legacy config file. No scanning, persistence,
refresh, subscription creation, chat send, marker or channel mutation is done.
"""

import argparse
import json
from pathlib import Path

from twitchbot.adapters.twitch import HelixClient, TwitchCredentials, TwitchFailure


def verify(path):
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('invalid explicit configuration file')
    data = json.loads(path.read_text(encoding='utf-8'))
    channel = data.get('broadcaster_id')
    token = data.get('broadcaster_token')
    if not token and data.get('bot_user_id') == channel:
        token = data.get('access_token')
    if not token:
        return {'state': 'broadcaster_authorization_not_configured'}
    try:
        client = HelixClient(TwitchCredentials(data.get('client_id'), channel, token), channel)
        result = client.validate()
        result['live'] = client.stream() is not None
        client.channel()
        result['channel_read'] = True
        return result
    except TwitchFailure as exc:
        return {'state': exc.code}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-config', required=True, type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.legacy_config)
    except (OSError, ValueError, TypeError, AttributeError):
        report = {'state': 'configuration_unavailable'}
    print(json.dumps(report))
