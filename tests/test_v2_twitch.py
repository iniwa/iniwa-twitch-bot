import pytest

from twitchbot.adapters.twitch import HelixClient, TwitchCredentials, TwitchFailure, HttpReply
from twitchbot.application.control import ChannelUpdate


@pytest.fixture
def helix():
    calls, queue, clock = [], [], [100.0]
    def transport(method, url, **kwargs):
        calls.append((method, url, kwargs))
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
    client = HelixClient(TwitchCredentials('client', '123', 'fixture-token'), '123',
        transport=transport, monotonic=lambda: clock[0], wall_time=lambda: 1000)
    queue.append(HttpReply(200, {'client_id': 'client', 'user_id': '123',
        'scopes': ['channel:manage:broadcast', 'moderator:read:followers'], 'expires_in': 7200}))
    return client, calls, queue, clock


def test_credentials_and_status_are_inert_and_do_not_disclose_tokens(helix):
    client, calls, _, _ = helix
    assert not client.available and not client.status()['read']
    assert 'fixture-token' not in repr(client.credentials)+str(client.status())
    assert calls == []
    with pytest.raises(TwitchFailure, match='authorization_validation_required'):
        client.stream()


@pytest.mark.parametrize('response,expected', [({'is_sent': True, 'message_id': 'm'}, 'sent'), ({'is_sent': False, 'drop_reason': {'code': 'blocked'}}, 'failed'), ({'is_sent': 'true'}, 'unknown')])
def test_chat_api_checks_sent_flag_and_single_attempt(helix, response, expected):
    client, calls, queue, _ = helix
    queue[0].data['scopes'].append('user:write:chat')
    client.validate()
    queue.extend([HttpReply(200, {'data': []}), HttpReply(200, {'data': [response]})])
    if expected == 'unknown':
        with pytest.raises(TwitchFailure) as error: client.send_chat('hello')
        assert error.value.uncertain
    else:
        assert client.send_chat('hello') == expected
    assert sum(call[0] == 'POST' for call in calls) == 1
    assert calls[-1][2]['json']['sender_id'] == '123'


def test_chat_shared_session_never_posts(helix):
    client, calls, queue, _ = helix
    client.validate(); queue.append(HttpReply(200, {'data': [{'session_id':'shared'}]}))
    with pytest.raises(TwitchFailure, match='shared_chat_paused'): client.send_chat('hello')
    assert not any(call[0] == 'POST' for call in calls)


def test_validation_is_cached_hourly_and_identity_checked(helix):
    client, calls, queue, clock = helix
    assert client.validate()['presets']
    client.validate()
    assert len(calls) == 1
    clock[0] += 3600
    assert not client.available
    queue.append(HttpReply(200, {'client_id': 'other', 'user_id': '123', 'scopes': [], 'expires_in': 7200}))
    with pytest.raises(TwitchFailure, match='authorization_identity_mismatch'):
        client.validate()
    assert not client.status()['read']


def test_401_revokes_and_429_blocks_further_calls(helix):
    client, calls, queue, clock = helix
    client.validate()
    queue.append(HttpReply(429, {}, {'Ratelimit-Remaining': '0', 'Ratelimit-Reset': '1060'}))
    with pytest.raises(TwitchFailure, match='rate_limited'):
        client.stream()
    with pytest.raises(TwitchFailure, match='rate_limited'):
        client.channel()
    assert len(calls) == 2
    clock[0] += 61
    queue.append(HttpReply(401))
    with pytest.raises(TwitchFailure, match='authorization_required'):
        client.stream()
    assert not client.available


@pytest.mark.parametrize('reply', [TimeoutError('private'), HttpReply(503), HttpReply(200, {'data': []})])
def test_uncertain_marker_is_never_retried(helix, reply):
    client, calls, queue, _ = helix
    client.validate()
    queue.append(reply)
    assert client.create_marker('123', 'note').state == 'unknown'
    assert len(calls) == 2


def test_marker_and_preset_payloads_and_no_send_on_invalid_input(helix):
    client, calls, queue, _ = helix
    client.validate()
    queue.append(HttpReply(200, {'data': [{'id': 'marker', 'position_seconds': 12}]}))
    assert client.create_marker('123', 'note').remote_id == 'marker'
    queue.append(HttpReply(204))
    assert client.apply_preset('123', ChannelUpdate('title', None, ('日本語',))).state == 'succeeded'
    assert calls[-1][2]['json'] == {'title': 'title', 'tags': ['日本語']}
    before = len(calls)
    assert client.create_marker('999', 'note').state == 'failed'
    assert client.create_marker('123', 'x'*141).state == 'failed'
    assert client.apply_preset('123', ChannelUpdate('title', None, ('with space',))).state == 'failed'
    assert len(calls) == before


def test_stream_response_channel_and_runtime_gate(helix):
    client, calls, queue, _ = helix
    client.validate()
    queue.append(HttpReply(200, {'data': [{'user_id': '999'}]}))
    with pytest.raises(TwitchFailure, match='invalid_response'):
        client.stream()
    client.allowed = lambda: False
    before = len(calls)
    assert client.create_marker('123', 'note').state == 'failed'
    assert len(calls) == before
