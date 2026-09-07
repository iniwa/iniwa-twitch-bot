from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sqlite3

import pytest

from twitchbot.adapters.persistence import SQLiteDatabase,StreamRepository,SettingsRepository
from twitchbot.adapters.persistence.backups import BackupService,BackupLimits
from twitchbot.adapters.persistence.community import CommunityRepository
from twitchbot.adapters.persistence.control import ControlRepository
from twitchbot.application.backups import TransferReceipt,daily_backup_due,retention_candidates
from twitchbot.application.backup_coordinator import BackupCoordinator
from twitchbot.application.community import ChatMessage,Person
from twitchbot.application.persistence import PersistenceError,StreamRecord
from twitchbot.settings import AppSettings

NOW=datetime(2026,9,6,tzinfo=timezone.utc)


def test_old_verified_backup_upgrades_only_new_restore_candidate(tmp_path, monkeypatch):
    from twitchbot.adapters.persistence.migrations import MIGRATIONS
    import twitchbot.adapters.persistence.backups as module
    old = SQLiteDatabase(tmp_path/'old.sqlite3', migrations=MIGRATIONS[:5]); old.migrate()
    stage = tmp_path/'backups'; stage.mkdir()
    service = BackupService(old, stage, limits=BackupLimits(reserve_bytes=0, reserve_fraction=0))
    with monkeypatch.context() as patch:
        patch.setattr(module, 'MIGRATIONS', MIGRATIONS[:5])
        item = service.create()
    original = (stage/item['id']/'snapshot.sqlite3').read_bytes()
    assert service.verify(item['id'])['schema_version'] == 5
    assert service.prepare_restore(item['id'], tmp_path/'restored.sqlite3')['state'] == 'candidate_verified'
    with SQLiteDatabase(tmp_path/'restored.sqlite3').connection() as c:
        assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == len(MIGRATIONS)
    assert (stage/item['id']/'snapshot.sqlite3').read_bytes() == original
    with old.connection() as c:
        assert c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 5


def test_abandoned_lock_file_is_not_an_active_backup_owner(setup):
    _, _, service = setup
    (service.root/'.backup.lock').write_bytes(b'old-crash')
    assert service.create()['state'] == 'local_ready'


@pytest.fixture
def setup(tmp_path):
    db=SQLiteDatabase(tmp_path/"candidate.sqlite3");db.migrate()
    StreamRepository(db).put(StreamRecord("s","c","fixture",None,None,None,(),NOW,None,None,"bot","partial",None,None,None,None,{},None),0)
    community=CommunityRepository(db,"c",clock=lambda:NOW)
    community.record_chat(ChatMessage("m",Person("u"),"s",NOW,NOW,"fixture body"))
    SettingsRepository(db).save(AppSettings(bot_enabled=True,welcome_enabled=True),0)
    root=tmp_path/"snapshots";root.mkdir()
    limits=BackupLimits(max_bytes=100*1024**2,reserve_bytes=0,reserve_fraction=0)
    return db,community,BackupService(db,root,limits=limits,clock=lambda:NOW)


def test_online_copy_preserves_snapshot_while_live_data_changes(setup):
    db,community,service=setup
    written=[]
    def during_copy():
        if not written:
            community.record_chat(ChatMessage("during-copy",Person("u"),"s",NOW,NOW,"concurrent body"))
            written.append(True)
        return False
    manifest=service.create(reasons=("daily","stream_end"),cancelled=during_copy)
    assert written
    assert manifest["state"]=="local_ready" and manifest["nas_verified_at"] is None
    assert manifest["counts"]["chat_messages"]==1
    community.record_chat(ChatMessage("later",Person("u"),"s",NOW,NOW,"later body"))
    assert service.verify(manifest["id"])["counts"]["chat_messages"]==1
    assert community.chats()["total"]==3
    assert "fixture body" not in json.dumps(manifest)
    from twitchbot.application.workers import ProcessLease
    lease = ProcessLease(service.root/".backup.lock")
    lease.acquire(); lease.release()  # inode remains; ownership does not


def test_queue_limits_and_cancellation_preserve_last_good_copy(setup):
    db,community,service=setup
    service.limits=BackupLimits(max_pending=1,max_bytes=100*1024**2,reserve_bytes=0,reserve_fraction=0)
    good=service.create()
    with pytest.raises(PersistenceError,match="backup_queue_full"):service.create()
    assert service.verify(good["id"])
    service.limits=BackupLimits(max_pending=2,max_bytes=100*1024**2,reserve_bytes=0,reserve_fraction=0)
    with pytest.raises(PersistenceError,match="backup_cancelled"):service.create(cancelled=lambda:True)
    assert [item["id"] for item in service.list_backups()]==[good["id"]]


def test_nas_failure_and_wrong_receipt_never_claim_remote_success(setup):
    db,community,service=setup
    manifest=service.create()
    with pytest.raises(PersistenceError,match="nas_transfer_unavailable"):service.publish(manifest["id"])
    class Wrong:
        def publish(self,path,key,checksum,metadata):return TransferReceipt(key,checksum,path.stat().st_size,False)
    service.transfer=Wrong()
    with pytest.raises(PersistenceError,match="nas_verification_failed"):service.publish(manifest["id"])
    retained=service.verify(manifest["id"])
    assert retained["state"]=="transfer_failed" and retained["nas_verified_at"] is None
    class Verified:
        calls=0
        def publish(self,path,key,checksum,metadata):
            self.calls+=1
            return TransferReceipt(key,checksum,path.stat().st_size,True)
    service.transfer=Verified()
    assert service.publish(manifest["id"])["state"]=="nas_verified"
    assert service.publish(manifest["id"])["state"]=="nas_verified"
    assert service.transfer.calls==1


def test_checksum_corruption_prevents_restore(setup,tmp_path):
    db,community,service=setup
    item=service.create()
    path=service.root/item["id"]/"snapshot.sqlite3"
    with path.open("ab") as output:output.write(b"corruption")
    with pytest.raises(PersistenceError,match="backup_checksum_mismatch"):
        service.prepare_restore(item["id"],tmp_path/"restored.sqlite3")
    assert not (tmp_path/"restored.sqlite3").exists()
    assert community.chats()["items"][0]["body"]=="fixture body"


def test_restore_is_a_new_stopped_candidate_and_can_return_old_bodies(setup,tmp_path):
    db,community,service=setup
    controls=ControlRepository(db,"c",clock=lambda:NOW)
    controls.create_note("pending","s","local note",marker=True)
    community.start_sync("pending-sync",0)
    item=service.create()
    preview=community.preview_body_deletion(NOW,NOW+timedelta(seconds=1))
    community.delete_chat_bodies(preview["id"])
    candidate=tmp_path/"restored.sqlite3"
    result=service.prepare_restore(item["id"],candidate)
    assert result["automatic_execution"]=="stopped" and result["chat_bodies_may_return"]
    restored=SQLiteDatabase(candidate)
    assert not SettingsRepository(restored).load().settings.bot_enabled
    assert CommunityRepository(restored,"c").chats()["items"][0]["body"]=="fixture body"
    assert community.chats()["items"][0]["body"] is None
    assert ControlRepository(restored,"c").operation("pending")["state"]=="unknown"
    assert controls.operation("pending")["state"]=="pending"
    with restored.connection() as c:
        assert c.execute("SELECT state FROM follower_sync_runs").fetchone()[0]=="failed"
        assert c.execute("SELECT COUNT(*) FROM channel_read_model").fetchone()[0]==0
    with pytest.raises(PersistenceError,match="restore_target_exists_or_unsafe"):
        service.prepare_restore(item["id"],db.path)


def test_schema_or_settings_with_unapproved_fields_are_not_exported(setup):
    db,community,service=setup
    with db.connection() as c:
        c.execute("INSERT INTO settings VALUES ('unknown_setting','1',1,?)",(NOW.isoformat(),));c.commit()
    with pytest.raises(PersistenceError):service.create()
    assert service.list_backups()==[]
    with db.connection() as c:
        c.execute("DELETE FROM settings WHERE key='unknown_setting'")
        c.execute("CREATE TABLE unapproved_data(value TEXT)");c.commit()
    with pytest.raises(PersistenceError,match="backup_schema_mismatch"):service.create()
    assert service.list_backups()==[]


def test_cross_instance_lease_and_invalid_restore_target_release_lock(setup):
    db,community,service=setup
    item=service.create()
    with pytest.raises(PersistenceError):service.prepare_restore(item["id"],Path("relative.sqlite3"))
    service._acquire()
    try:
        another=BackupService(db,service.root,limits=service.limits)
        with pytest.raises(PersistenceError,match="backup_busy"):another.create()
    finally:service._release()
    assert service.verify(item["id"])


def test_daily_schedule_uses_jst_four_and_pauses_with_full_stop():
    before=datetime(2026,9,5,18,59,tzinfo=timezone.utc)
    after=before+timedelta(minutes=1)
    assert daily_backup_due(before,"2026-09-05",running=True) is None
    assert daily_backup_due(after,"2026-09-05",running=True)=="2026-09-06"
    assert daily_backup_due(after,"2026-09-05",running=False) is None
    assert daily_backup_due(after,"2026-08-01",running=True)=="2026-09-06"


def test_retention_never_selects_manual_untransferred_or_last_success():
    items=[{"id":str(day),"state":"nas_verified","created_at":(NOW-timedelta(days=day)).isoformat(),"reasons":["daily","stream_end"]} for day in range(40)]
    items.extend([{"id":"manual","state":"nas_verified","created_at":(NOW-timedelta(days=100)).isoformat(),"reasons":["manual"]},{"id":"pending","state":"local_ready","created_at":NOW.isoformat(),"reasons":["daily"]}])
    remove=retention_candidates(items)
    assert "39" in remove
    assert not set(remove)&{"manual","pending","0","1","2"}


class RetentionTransfer:
    def __init__(self):
        self.retired = []
        self.payloads = {}
        self.fail_verify = False
        self.fail_retire = False

    def publish(self, path, key, digest, metadata):
        self.payloads[key] = path.read_bytes()
        return TransferReceipt(key, digest, path.stat().st_size, True)

    def fetch(self, key, metadata, destination):
        receipt = self.verify(metadata)
        with destination.open('xb') as out: out.write(self.payloads[key])
        return receipt

    def verify(self, metadata):
        if self.fail_verify: raise PersistenceError('nas_unavailable', 'backup')
        assert metadata['id'] not in self.retired
        return TransferReceipt(metadata['id'], metadata['checksum'], metadata['size_bytes'], True)

    def retire(self, metadata):
        if self.fail_retire: raise PersistenceError('nas_interrupted', 'backup')
        self.retired.append(metadata['id'])


def retention_series(service):
    transfer = RetentionTransfer()
    service.transfer = transfer
    items = []
    for day in range(18):
        at = NOW+timedelta(days=day)
        service.clock = lambda at=at: at
        item = service.create(reasons=('daily',))
        items.append(service.publish(item['id']))
    return transfer, items


def test_retention_keeps_latest_manual_and_restore_reservations(setup):
    _, _, service = setup
    transfer, items = retention_series(service)
    manual = service.create(); service.publish(manual['id'])
    candidates = retention_candidates(service.list_backups())
    assert candidates
    protected = candidates[0]
    accepted = service.reserve_restore(protected, lambda: 'queued')
    assert accepted == 'queued'
    retired = service.maintain_retention(protected_ids=lambda: [protected])
    assert set(retired) == set(candidates)-{protected}
    assert not set(retired) & {manual['id'], items[-1]['id'], protected}
    for key in retired:
        assert not (service.root/key/'snapshot.sqlite3').exists()
        assert (service.root/key/'manifest.json').exists()
        with pytest.raises(PersistenceError, match='backup_retired'):
            service.verify(key)
        with pytest.raises(PersistenceError, match='backup_retired_or_missing'):
            service.reserve_restore(key, lambda: pytest.fail('retired copy accepted'))
    assert service.maintain_retention(protected_ids=[protected]) == ()


def test_retention_failure_preserves_copies_and_restarts_durable_intent(setup):
    _, _, service = setup
    transfer, items = retention_series(service)
    candidates = retention_candidates(items)
    transfer.fail_verify = True
    with pytest.raises(PersistenceError, match='nas_unavailable'):
        service.maintain_retention()
    assert all(m['state'] == 'nas_verified' for m in service.list_backups())
    transfer.fail_verify = False; transfer.fail_retire = True
    with pytest.raises(PersistenceError, match='nas_interrupted'):
        service.maintain_retention()
    pending = [m for m in service.list_backups() if m['state'] == 'retiring']
    assert len(pending) == 1
    assert (service.root/pending[0]['id']/'snapshot.sqlite3').exists()
    assert service.verify(items[-1]['id'])['state'] == 'nas_verified'
    restarted = BackupService(service.database, service.root, limits=service.limits,
                              transfer=transfer, clock=service.clock)
    transfer.fail_retire = False
    assert set(restarted.maintain_retention()) == set(candidates)
    assert len(restarted.list_backups()) == len(items)


def test_retention_and_restore_acceptance_share_ownership(setup):
    _, _, service = setup
    item = service.create()
    service._acquire()
    try:
        with pytest.raises(PersistenceError, match='backup_busy'):
            service.reserve_restore(item['id'], lambda: pytest.fail('ownership bypass'))
    finally:
        service._release()


def test_retention_rejects_unverified_receipt(setup):
    _, _, service = setup
    transfer, items = retention_series(service)
    transfer.verify = lambda m: TransferReceipt(m['id'], m['checksum'], m['size_bytes'], False)
    with pytest.raises(PersistenceError, match='nas_verification_failed'):
        service.maintain_retention()
    assert not transfer.retired
    assert all(m['state'] == 'nas_verified' for m in service.list_backups())


def test_coordinator_combines_daily_and_stream_end_and_deduplicates_after_restart(setup):
    db,community,service=setup
    stopped=BackupCoordinator(service)
    assert stopped.step(["s"])["state"]=="paused"
    assert service.list_backups()==[]
    active=BackupCoordinator(service,running=lambda:True)
    assert active.step(["s"])["state"]=="local_ready"
    item=service.list_backups()[0]
    assert item["reasons"]==["daily","stream_end"] and item["stream_ids"]==["s"]
    restarted=BackupCoordinator(service,running=lambda:True)
    assert restarted.step(["s"])["state"]=="up_to_date"
    assert len(service.list_backups())==1


def test_coordinator_retains_unprocessed_request_when_capacity_is_full(setup):
    db,community,service=setup
    service.limits=BackupLimits(max_pending=1,max_bytes=100*1024**2,reserve_bytes=0,reserve_fraction=0)
    service.create()
    coordinator=BackupCoordinator(service,running=lambda:True)
    assert coordinator.step(["s"])["reason"]=="backup_queue_full"
    assert coordinator.step(["s"])["state"]=="waiting"
    assert service.list_backups()[0]["stream_ids"]==[]


def test_local_compaction_preserves_protected_copies_and_restores_from_nas(setup):
    _, _, service = setup
    transfer, items = retention_series(service)
    manual = service.create();service.publish(manual['id'])
    before = service.create(reasons=('before_change',));service.publish(before['id'])
    pending = service.create(reasons=('daily',))
    protected = items[2]['id']
    newest = next(i['id'] for i in service.list_backups() if i['state'] == 'nas_verified')
    released = service.compact_local(protected_ids=lambda: [protected])
    assert released and protected not in released
    for key in (manual['id'], before['id'], pending['id'], protected, newest):
        assert (service.root/key/'snapshot.sqlite3').is_file()
    assert not transfer.retired
    key = released[0]
    assert not (service.root/key/'snapshot.sqlite3').exists()
    assert next(m for m in service.list_backups() if m['id'] == key)['state'] == 'nas_verified'
    assert service.compact_local(protected_ids=[protected]) == ()
    assert service.reserve_restore(key,lambda:'queued') == 'queued'
    service.retrieve(key)
    assert service.verify(key)['state'] == 'nas_verified'
    assert service.prepare_restore(key,service.root.parent/'compacted-restore.sqlite3')['automatic_execution'] == 'stopped'


def test_local_compaction_stops_on_unverified_remote_and_keeps_last_local(setup):
    _, _, service = setup
    transfer, items = retention_series(service)
    transfer.fail_verify = True
    with pytest.raises(PersistenceError,match='nas_unavailable'):service.compact_local()
    assert all((service.root/i['id']/'snapshot.sqlite3').exists() for i in items)
    transfer.fail_verify = False
    newest = service.list_backups()[0]
    transfer.verify = lambda m: TransferReceipt(m['id'],m['checksum'],m['size_bytes'],m['id']==newest['id'])
    with pytest.raises(PersistenceError,match='nas_verification_failed'):service.compact_local()
    assert all((service.root/i['id']/'snapshot.sqlite3').exists() for i in items)


def test_compacted_generations_can_later_retire_without_retrieving_payload(setup):
    _, _, service = setup
    transfer, items = retention_series(service)
    candidates = set(retention_candidates(items))
    assert candidates <= set(service.compact_local())
    assert set(service.maintain_retention()) == candidates
    assert set(transfer.retired) == candidates
    assert service.verify(items[-1]['id'])['state'] == 'nas_verified'


def test_compaction_shares_restore_ownership_and_keeps_corrupt_latest(setup):
    _, _, service = setup
    transfer, items = retention_series(service)
    service._acquire()
    try:
        with pytest.raises(PersistenceError,match='backup_busy'):service.compact_local()
    finally:service._release()
    (service.root/items[-1]['id']/'snapshot.sqlite3').write_bytes(b'corrupt')
    with pytest.raises(PersistenceError,match='backup_checksum_mismatch'):service.compact_local()
    assert all((service.root/i['id']/'snapshot.sqlite3').exists() for i in items)
