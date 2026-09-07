"""Exercise retention only in newly created synthetic local/NAS QA directories."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from twitchbot.adapters.nas import MountedNasTransfer, linux_mount_identity
from twitchbot.adapters.persistence import SQLiteDatabase
from twitchbot.adapters.persistence.backups import BackupLimits, BackupService
from twitchbot.application.backups import retention_candidates
from twitchbot.application.persistence import PersistenceError


def canonical_directory(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_dir():
        raise argparse.ArgumentTypeError('An existing canonical absolute directory is required')
    return path


def run(work_root, nas_root, expected_source):
    linux_mount_identity(nas_root, expected_source)
    local = Path(tempfile.mkdtemp(prefix='retention-v2-', dir=work_root))
    remote = Path(tempfile.mkdtemp(prefix='retention-v2-', dir=nas_root))
    db = SQLiteDatabase(local/'synthetic.sqlite3'); db.migrate()
    stage = local/'snapshots'; stage.mkdir()
    adapter = MountedNasTransfer(remote, expected_source, reserve_bytes=5*1024**2)
    limits = BackupLimits(max_bytes=100*1024**2, reserve_bytes=5*1024**2, reserve_fraction=0)
    service = BackupService(db, stage, transfer=adapter, limits=limits)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    copies = []
    for day in range(18):
        at = base+timedelta(days=day)
        service.clock = lambda at=at: at
        item = service.create(reasons=('daily',))
        copies.append(service.publish(item['id']))
    manual = service.create(); service.publish(manual['id'])
    candidates = retention_candidates(service.list_backups())
    assert len(candidates) >= 2
    reserved = candidates[0]
    service.reserve_restore(reserved, lambda: True)
    original = adapter.retire
    interrupted = []

    def after_remote_deletion(metadata):
        original(metadata)
        interrupted.append(metadata['id'])
        raise PersistenceError('synthetic_interruption', 'backup')

    adapter.retire = after_remote_deletion
    try:
        service.maintain_retention(protected_ids=[reserved])
    except PersistenceError as exc:
        assert exc.code == 'synthetic_interruption'
    else:
        raise AssertionError('Expected synthetic interruption')
    assert len(interrupted) == 1
    assert next(m for m in service.list_backups() if m['id'] == interrupted[0])['state'] == 'retiring'
    adapter.retire = original
    restarted = BackupService(db, stage, transfer=adapter, limits=limits, clock=service.clock)
    retired = restarted.maintain_retention(protected_ids=[reserved])
    assert set(retired) == set(candidates)-{reserved}
    assert restarted.maintain_retention(protected_ids=[reserved]) == ()
    assert adapter.verify(restarted.verify(reserved)).destination_verified
    assert adapter.verify(restarted.verify(manual['id'])).destination_verified
    assert adapter.verify(restarted.verify(copies[-1]['id'])).destination_verified
    assert all(not (remote/key).exists() and not (stage/key/'snapshot.sqlite3').exists() for key in retired)
    assert len(restarted.list_backups()) == 19
    released = restarted.compact_local(protected_ids=[reserved])
    assert released and reserved not in released and manual['id'] not in released
    assert restarted.compact_local(protected_ids=[reserved]) == ()
    metadata = next(m for m in restarted.list_backups() if m['id'] == released[0])
    assert metadata['state'] == 'nas_verified'
    assert not (stage/metadata['id']/'snapshot.sqlite3').exists()
    assert adapter.verify(metadata).destination_verified
    restarted.retrieve(metadata['id'])
    restored = restarted.prepare_restore(metadata['id'], local/'compacted-restore.sqlite3')
    assert restored['automatic_execution'] == 'stopped'
    report = dict(state='retention_verified', copies=19, retired=len(retired),
                  protected_restore=True, manual_retained=True, newest_retained=True,
                  interrupted_retirement_resumed=True, repeated_run='no_change',
                  local_payloads_released=len(released), nas_generations_preserved=True,
                  compacted_restore_verified=True,
                  local_artifact=local.name, nas_artifact=remote.name)
    (local/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', required=True, type=canonical_directory)
    parser.add_argument('--nas-root', required=True, type=canonical_directory)
    parser.add_argument('--expected-nas-source', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.work_root, args.nas_root, args.expected_nas_source), indent=2))
