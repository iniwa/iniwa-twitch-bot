"""Explicit synthetic SQLite/NAS smoke check; never opens a live database.

Run with PYTHONPATH=src. Work and NAS roots must already exist. Each run creates
new child directories and retains them for inspection. No scheduler is started.
NAS mode is Linux-only and requires an exact expected mount source.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import tempfile
from time import perf_counter

from twitchbot.adapters.persistence import SQLiteDatabase, StreamRepository, SettingsRepository
from twitchbot.adapters.persistence.backups import BackupService, BackupLimits, checksum
from twitchbot.adapters.persistence.community import CommunityRepository
from twitchbot.adapters.persistence.analytics import AnalyticsRepository, HistoryReader
from twitchbot.adapters.nas import MountedNasTransfer
from twitchbot.application.analytics import CollectionRun, ViewerObservation
from twitchbot.application.community import ChatMessage, Person
from twitchbot.application.persistence import StreamRecord
from twitchbot.settings import AppSettings


def canonical_directory(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir() or path.resolve() != path or path.is_symlink():
        raise ValueError('An existing canonical absolute directory is required')
    return path


def network_mount(root, expected_source):
    """Check the deepest mount, including bind mounts; reject local fallbacks."""
    def decode(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)

    matches = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields, filesystem = left.split(), right.split()
        target = Path(decode(fields[4]))
        if root.is_relative_to(target):
            matches.append((len(target.parts), fields[0], filesystem[0], decode(filesystem[1])))
    if not matches:
        raise ValueError('No mount identity found')
    identity = max(matches, key=lambda item: (item[0], int(item[1])))
    if identity[2] not in ('cifs', 'nfs', 'nfs4') or identity[3] != expected_source:
        raise ValueError('NAS mount identity mismatch')
    return identity


def run(work_root, nas_root=None, expected_source=None):
    identity = network_mount(nas_root, expected_source) if nas_root else None
    area = Path(tempfile.mkdtemp(prefix='v2-device-check-', dir=work_root))
    db = SQLiteDatabase(area/'synthetic.sqlite3')
    db.migrate()
    now = datetime.now(timezone.utc)
    start = now-timedelta(hours=6)
    StreamRepository(db).put(StreamRecord('synthetic-stream', 'synthetic-channel', 'Device QA',
        None, None, None, (), start, None, None, 'bot', 'partial',
        None, None, None, None, {}, None), 0)
    SettingsRepository(db).save(AppSettings(bot_enabled=True, welcome_enabled=True), 0)
    community = CommunityRepository(db, 'synthetic-channel')
    analytics = AnalyticsRepository(db)
    analytics.start_run('synthetic-stream', CollectionRun('synthetic-run', start))
    for i in range(1080):
        analytics.append('synthetic-stream', ViewerObservation('synthetic-run',
            start+timedelta(seconds=20*i), 10+i % 41))
    reader = HistoryReader(db)
    latencies = []

    def write():
        for i in range(240):
            at = start+timedelta(seconds=30*i)
            community.record_chat(ChatMessage('synthetic-'+str(i), Person('viewer-'+str(i % 12)),
                'synthetic-stream', at, now, 'Synthetic device verification'))

    def read():
        measured = []
        for _ in range(40):
            before = perf_counter()
            reader.detail('synthetic-stream')
            community.people(stream_id='synthetic-stream')
            community.chats(stream_id='synthetic-stream')
            measured.append(1000*(perf_counter()-before))
        return measured

    begin = perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        writer = pool.submit(write)
        readers = [pool.submit(read) for _ in range(3)]
        writer.result()
        for future in readers:
            latencies.extend(future.result())
    assert community.chats()['total'] == 240
    db.quick_check()
    staging = area/'snapshots'
    staging.mkdir()
    limits = BackupLimits(max_bytes=100*1024**2, reserve_bytes=5*1024**2, reserve_fraction=0)
    service = BackupService(db, staging, limits=limits)
    item = service.create(reasons=('daily', 'stream_end'), stream_ids=('synthetic-stream',))
    assert service.verify(item['id'])['counts']['chat_messages'] == 240
    restore_service = service
    nas_result = {'state': 'not_requested'}

    if nas_root:
        assert network_mount(nas_root, expected_source) == identity
        remote = Path(tempfile.mkdtemp(prefix='v2-device-check-', dir=nas_root))
        service.transfer = MountedNasTransfer(remote, expected_source, reserve_bytes=5*1024**2)
        item = service.publish(item['id'])
        assert item['state'] == 'nas_verified'
        assert network_mount(nas_root, expected_source) == identity
        folder = remote/item['id']
        assert checksum(folder/'snapshot.sqlite3') == item['checksum']
        # Remove only this run's synthetic local payload, then fetch through
        # the real adapter before constructing the restore candidate.
        local_payload = staging/item['id']/'snapshot.sqlite3'
        assert local_payload.resolve().is_relative_to(area.resolve())
        local_payload.unlink()
        service.retrieve(item['id'])
        assert checksum(local_payload) == item['checksum']
        restore_service = service
        nas_result = {'state': 'copy_readback_verified', 'filesystem': identity[2],
                      'bytes': item['size_bytes'], 'artifact_directory': remote.name}

    result = restore_service.prepare_restore(item['id'], area/'restored.sqlite3')
    restored = SQLiteDatabase(area/'restored.sqlite3')
    assert result['automatic_execution'] == 'stopped'
    assert not SettingsRepository(restored).load().settings.bot_enabled
    assert CommunityRepository(restored, 'synthetic-channel').chats()['total'] == 240
    assert SettingsRepository(db).load().settings.bot_enabled
    latencies.sort()
    report = {'python': platform.python_version(), 'architecture': platform.machine(),
              'sqlite': sqlite3.sqlite_version, 'viewer_observations': 1080,
              'concurrent_chat_writes': 240, 'read_batches': len(latencies),
              'read_batch_p95_ms': round(latencies[int(.95*(len(latencies)-1))], 2),
              'read_batch_max_ms': round(max(latencies), 2),
              'elapsed_seconds': round(perf_counter()-begin, 2),
              'nas': nas_result, 'restore': result['state'],
              'artifact_directory': area.name}
    (area/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', required=True, type=canonical_directory)
    parser.add_argument('--nas-root', type=canonical_directory)
    parser.add_argument('--expected-nas-source')
    args = parser.parse_args()
    if bool(args.nas_root) != bool(args.expected_nas_source):
        parser.error('--nas-root and --expected-nas-source must be supplied together')
    print(json.dumps(run(args.work_root, args.nas_root, args.expected_nas_source), indent=2))
