from hashlib import sha256
import os
import pytest

from twitchbot.adapters.nas import MountedNasTransfer, linux_mount_identity
from twitchbot.application.persistence import PersistenceError


def test_nas_constructor_is_inert(tmp_path):
    path = tmp_path/'missing'
    MountedNasTransfer(path, '//example/share')
    assert not path.exists()


@pytest.fixture
def nas(tmp_path):
    if os.name != 'posix':
        pytest.skip('NAS directory-descriptor adapter targets Linux; exercised on arm64')
    root = tmp_path/'remote'
    root.mkdir()
    source = tmp_path/'synthetic.sqlite3'
    source.write_bytes(b'synthetic NAS transfer fixture')
    key, digest = 'a'*32, sha256(source.read_bytes()).hexdigest()
    metadata = {'id': key, 'checksum': digest, 'size_bytes': source.stat().st_size,
                'state': 'local_ready', 'counts': {}, 'reasons': ['manual']}
    adapter = MountedNasTransfer(root, 'fixture', reserve_bytes=0,
                                 mount_probe=lambda *_: ('synthetic-mounted-share',))
    return adapter, source, key, digest, metadata


def test_nas_copy_verifies_and_idempotently_recognizes_completion(nas):
    adapter, source, key, digest, metadata = nas
    receipt = adapter.publish(source, key, digest, metadata)
    assert receipt.destination_verified
    assert adapter.publish(source, key, digest, metadata) == receipt
    assert (adapter.root/key/'snapshot.sqlite3').read_bytes() == source.read_bytes()
    assert len(list(adapter.root.iterdir())) == 1


def test_nas_conflicting_existing_copy_is_never_overwritten(nas):
    adapter, source, key, digest, metadata = nas
    adapter.publish(source, key, digest, metadata)
    remote = adapter.root/key/'snapshot.sqlite3'
    remote.write_bytes(b'changed')
    with pytest.raises(PersistenceError, match='nas_backup_conflict'):
        adapter.publish(source, key, digest, metadata)
    assert remote.read_bytes() == b'changed'


def test_nas_changed_mount_and_cancel_leave_no_completed_copy(nas):
    adapter, source, key, digest, metadata = nas
    calls = []
    def changed(*_):
        calls.append(1)
        return ('first' if len(calls) <= 2 else 'replacement',)
    adapter.probe = changed
    with pytest.raises(PersistenceError, match='nas_destination_changed'):
        adapter.publish(source, key, digest, metadata)
    assert list(adapter.root.iterdir()) == []
    adapter.probe = lambda *_: ('same',)
    adapter.cancelled = lambda: True
    with pytest.raises(PersistenceError, match='backup_cancelled'):
        adapter.publish(source, key, digest, metadata)
    assert list(adapter.root.iterdir()) == []


def test_nas_symlinks_unmanaged_area_and_corruption_are_rejected(nas, tmp_path):
    adapter, source, key, digest, metadata = nas
    other = tmp_path/'unrelated'
    other.mkdir()
    (adapter.root/key).symlink_to(other, target_is_directory=True)
    with pytest.raises(PersistenceError):
        adapter.publish(source, key, digest, metadata)
    assert list(other.iterdir()) == []
    (adapter.root/key).unlink()
    (adapter.root/'unrelated.txt').write_text('keep')
    with pytest.raises(PersistenceError, match='nas_area_unmanaged'):
        adapter.publish(source, key, digest, metadata)
    (adapter.root/'unrelated.txt').unlink()
    source.write_bytes(b'bad')
    with pytest.raises(PersistenceError, match='backup_checksum_mismatch'):
        adapter.publish(source, key, digest, metadata)
    assert list(adapter.root.iterdir()) == []


def test_linux_local_directory_is_not_accepted_as_a_nas(nas):
    adapter, *_ = nas
    with pytest.raises(PersistenceError, match='nas_mount_unverified'):
        linux_mount_identity(adapter.root, '//wrong/share')


def test_nas_fetch_reads_verified_bytes_without_overwriting(nas, tmp_path):
    adapter, source, key, digest, metadata = nas
    adapter.publish(source, key, digest, metadata)
    target = tmp_path/'retrieved.sqlite3'
    assert adapter.fetch(key, metadata, target).destination_verified
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(PersistenceError, match='unsafe_nas_fetch_target'):
        adapter.fetch(key, metadata, target)
    assert target.read_bytes() == source.read_bytes()


def test_nas_fetch_rejects_corruption_and_changed_mount(nas, tmp_path):
    adapter, source, key, digest, metadata = nas
    adapter.publish(source, key, digest, metadata)
    (adapter.root/key/'snapshot.sqlite3').write_bytes(b'corrupt')
    target = tmp_path/'retrieved.sqlite3'
    with pytest.raises(PersistenceError): adapter.fetch(key, metadata, target)
    assert not target.exists()
    (adapter.root/key/'snapshot.sqlite3').write_bytes(source.read_bytes())
    adapter.cancelled = lambda: True
    with pytest.raises(PersistenceError): adapter.fetch(key, metadata, target)
    assert not target.exists()


def test_retirement_is_idempotent_and_preserves_other_copies(nas):
    adapter, source, key, digest, metadata = nas
    adapter.publish(source, key, digest, metadata)
    other = dict(metadata, id='b'*32)
    adapter.publish(source, other['id'], digest, other)
    adapter.retire(metadata)
    adapter.retire(metadata)
    assert not (adapter.root/key).exists()
    assert not (adapter.root/('.delete-'+key)).exists()
    assert adapter.verify(other).destination_verified


@pytest.mark.parametrize('partial', ['complete', 'manifest_only', 'empty'])
def test_retirement_resumes_after_interruption(nas, partial):
    adapter, source, key, digest, metadata = nas
    adapter.publish(source, key, digest, metadata)
    pending = adapter.root/('.delete-'+key)
    (adapter.root/key).rename(pending)
    if partial != 'complete': (pending/'snapshot.sqlite3').unlink()
    if partial == 'empty': (pending/'manifest.json').unlink()
    adapter.retire(metadata)
    assert not pending.exists()


def test_retirement_rejects_unknown_files_and_bad_partial_manifest(nas):
    adapter, source, key, digest, metadata = nas
    adapter.publish(source, key, digest, metadata)
    extra = adapter.root/key/'keep.txt'
    extra.write_text('unrelated')
    with pytest.raises(PersistenceError, match='nas_area_unmanaged'):
        adapter.retire(metadata)
    assert extra.read_text() == 'unrelated'
    assert adapter.verify(metadata).destination_verified
    extra.unlink()
    pending = adapter.root/('.delete-'+key)
    (adapter.root/key).rename(pending)
    (pending/'snapshot.sqlite3').unlink()
    (pending/'manifest.json').write_text('[]')
    with pytest.raises(PersistenceError, match='nas_backup_conflict'):
        adapter.retire(metadata)
    assert (pending/'manifest.json').read_text() == '[]'


def test_retirement_rejects_changed_mount_before_any_removal(nas):
    adapter, source, key, digest, metadata = nas
    adapter.publish(source, key, digest, metadata)
    count = 0
    def changed(*_):
        nonlocal count
        count += 1
        return ('first' if count <= 2 else 'changed',)
    adapter.probe = changed
    with pytest.raises(PersistenceError, match='nas_destination_changed'):
        adapter.retire(metadata)
    assert (adapter.root/key/'snapshot.sqlite3').read_bytes() == source.read_bytes()
