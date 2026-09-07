from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from twitchbot.adapters.persistence import SQLiteDatabase
from twitchbot.adapters.persistence.automation import AutomationRepository
from twitchbot.adapters.twitch import TwitchFailure
from twitchbot.application.chat_worker import ChatWorker
from twitchbot.application.live import LiveSnapshot, StreamSnapshot
from twitchbot.application.persistence import PersistenceError

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
COMMAND = dict(trigger='!sns', aliases=['!links'], response_type='text', body='Hello', role='everyone', shared_seconds=30, user_seconds=60)
POST = dict(body='Notice', target='all', category_id=None, minutes=1, comments=2)


@pytest.fixture
def chat(tmp_path):
    db = SQLiteDatabase(tmp_path/'core.sqlite3'); db.migrate()
    clock = [NOW]
    repo = AutomationRepository(db, '123', clock=lambda: clock[0])
    class Bot:
        credentials = SimpleNamespace(user_id='999')
        scopes = {'user:write:chat'}
        sent = []
        outcome = 'sent'
        shared = False
        def validate(self): pass
        def shared_chat_active(self): return self.shared
        def send_chat(self, body):
            self.sent.append(body)
            if isinstance(self.outcome, Exception): raise self.outcome
            return self.outcome
    live = SimpleNamespace(snapshot=lambda: LiveSnapshot(stream=StreamSnapshot(state='live', id='s1', title='Stream', game='Game', observed_at=clock[0], started_at=NOW)))
    worker = ChatWorker(repo, live, Bot(), running=lambda: True, connected=lambda: True)
    return repo, worker, clock


def define(repo, kind='command', key='d1', spec=None):
    result = repo.save_definition(key, kind, 'Name', False, spec or (COMMAND if kind == 'command' else POST), 0)
    return repo.save_definition(key, kind, 'Name', True, result['specification'], 1)


def incoming(worker, clock, key='m1', user='2', text='!SNS', **extra):
    worker.on_chat(dict(message_id=key, chatter_user_id=user, message={'text': text}, badges=[], **extra), 's1', clock[0])


def test_default_off_save_preview_and_alias_collision(chat):
    repo, worker, clock = chat
    define(repo)
    assert not repo.policy()['commands_enabled']
    incoming(worker, clock)
    assert worker.step()['state'] == 'paused'
    assert not worker.client.sent
    assert worker.preview(COMMAND, ' !sns ', 'everyone')['response'] == 'Hello'
    assert not worker.client.sent
    with pytest.raises(PersistenceError, match='command_name_conflict'):
        repo.save_definition('d2', 'command', 'Other', False, dict(COMMAND, trigger='!LINKS', aliases=[]), 0)


def test_definition_display_sort_and_filter_do_not_change_execution_order(chat):
    repo, worker, clock = chat
    first = repo.save_definition('first', 'post', 'Ｂeta', False, POST, 0, 1)
    clock[0] += timedelta(minutes=1)
    second = repo.save_definition('second', 'post', 'alpha', False, POST, 0, 9)
    clock[0] += timedelta(minutes=1)
    enabled = repo.save_definition('first', 'post', 'Ｂeta', True, POST, first['revision'], 1)
    assert first['created_at'] and enabled['created_at'] == first['created_at']
    assert enabled['updated_at'] > first['updated_at']
    assert [item['id'] for item in repo.definitions(sort='name')] == ['second', 'first']
    assert [item['id'] for item in repo.definitions(sort='updated_at', order='desc')] == ['first', 'second']
    assert [item['id'] for item in worker.snapshot(enabled=True)['definitions']] == ['first']
    assert [item['id'] for item in repo.definitions()] == ['first', 'second']
    with pytest.raises(PersistenceError, match='invalid_sort'):
        repo.definitions(sort='name DESC')


def test_alias_cooldown_dedup_and_atomic_queue(chat):
    repo, worker, clock = chat
    define(repo); repo.save_policy(True, False, [], 0)
    incoming(worker, clock)
    incoming(worker, clock, key='m2', text='!links')
    incoming(worker, clock)
    assert len(repo.snapshot()['results']) == 1
    assert worker.step()['state'] == 'sent'
    clock[0] += timedelta(seconds=31)
    incoming(worker, clock, key='m3')
    assert len(repo.snapshot()['results']) == 1
    incoming(worker, clock, key='m4', user='3')
    worker.step()
    assert worker.client.sent == ['Hello', 'Hello']


def test_queue_expires_and_edit_cancels_old_body(chat):
    repo, worker, clock = chat
    define(repo); repo.save_policy(True, False, [], 0)
    incoming(worker, clock)
    clock[0] += timedelta(seconds=16)
    worker.step()
    assert repo.snapshot()['results'][0]['reason'] == 'expired'
    clock[0] += timedelta(seconds=60)
    incoming(worker, clock, key='m2')
    repo.save_definition('d1', 'command', 'Name', True, dict(COMMAND, body='Changed'), 2)
    worker.step()
    assert not worker.client.sent


def test_roles_and_shared_chat_and_bot_self(chat):
    repo, worker, clock = chat
    define(repo, spec=dict(COMMAND, role='moderator')); repo.save_policy(True, False, [], 0)
    incoming(worker, clock)
    incoming(worker, clock, key='bot', user='999')
    incoming(worker, clock, key='other', user='123', source_broadcaster_user_id='555')
    assert not repo.snapshot()['results']
    incoming(worker, clock, key='owner', user='123')
    worker.client.shared = True
    assert worker.step()['state'] == 'shared_chat_paused'
    assert not worker.client.sent


def test_unknown_never_retried_and_recovery_discards_candidates(chat):
    repo, worker, clock = chat
    define(repo); repo.save_policy(True, False, [], 0)
    incoming(worker, clock)
    worker.client.outcome = TwitchFailure('transport_failed', uncertain=True)
    assert worker.step()['state'] == 'unknown'
    clock[0] += timedelta(seconds=60)
    incoming(worker, clock)  # same original message after cooldown
    repo.recover(); worker.step()
    assert len(worker.client.sent) == 1


def test_post_needs_time_and_comments_rename_preserves_wait(chat):
    repo, worker, clock = chat
    define(repo, 'post'); repo.save_policy(False, True, [], 0)
    worker.step()
    incoming(worker, clock, text='a'); incoming(worker, clock, key='m2', text='b')
    clock[0] += timedelta(seconds=59); worker.step()
    assert not worker.client.sent
    repo.save_definition('d1', 'post', 'Renamed', True, POST, 2, 4)
    clock[0] += timedelta(seconds=1)
    assert worker.step()['state'] == 'sent'
    assert worker.client.sent == ['Notice']
    clock[0] += timedelta(minutes=2); worker.step()
    assert len(worker.client.sent) == 1


def test_default_suppressed_by_waiting_specific_and_commands_prioritized(chat):
    repo, worker, clock = chat
    define(repo, 'post', 'default', dict(POST, target='default', comments=0))
    define(repo, 'post', 'specific', dict(POST, target='category', category_id='42', comments=50))
    define(repo)
    with repo.transaction(write=True) as c:
        c.execute("INSERT INTO channel_read_model VALUES ('123','Title','42','Game','[]',NULL,?,'helix',1)", (NOW.isoformat(),))
    repo.save_policy(True, True, [], 0)
    worker.step(); clock[0] += timedelta(minutes=2); worker.step()
    assert not worker.client.sent
    incoming(worker, clock); worker.step()
    assert worker.client.sent == ['Hello']


def test_command_queue_bound_with_zero_cooldowns(chat):
    repo, worker, clock = chat
    define(repo, spec=dict(COMMAND, shared_seconds=0, user_seconds=0))
    repo.save_policy(True, False, [], 0)
    for n in range(25): incoming(worker, clock, key=f'm{n}')
    assert len(repo.snapshot()['results']) == 20


def test_restore_disables_all_automation(chat, tmp_path):
    from twitchbot.adapters.persistence.backups import BackupService, BackupLimits
    repo, worker, clock = chat
    define(repo); repo.save_policy(True, True, [], 0); incoming(worker, clock)
    stage = tmp_path/'stage'; stage.mkdir()
    service = BackupService(repo.database, stage, limits=BackupLimits(reserve_bytes=0, reserve_fraction=0))
    item = service.create(); service.prepare_restore(item['id'], tmp_path/'restored.sqlite3')
    restored = AutomationRepository(SQLiteDatabase(tmp_path/'restored.sqlite3'), '123')
    assert not restored.policy()['commands_enabled'] and not restored.policy()['posts_enabled']
    assert not restored.definitions()[0]['enabled']
    assert restored.snapshot()['results'][0]['state'] == 'unknown'
