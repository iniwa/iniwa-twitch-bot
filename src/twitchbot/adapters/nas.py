"""Explicit Linux mounted-NAS transfer. Never mount shares or load credentials."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import errno
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from ..application.backups import TransferReceipt
from ..application.persistence import PersistenceError

_KEY = re.compile(r'[a-f0-9]{32}')
_DIGEST = re.compile(r'[a-f0-9]{64}')
_FIELDS = {'id', 'state', 'reasons', 'created_at', 'copy_completed_at',
           'schema_version', 'size_bytes', 'checksum', 'counts', 'nas_verified_at',
           'stream_ids', 'daily_day', 'error'}


def linux_mount_identity(path, expected_source):
    def decode(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    try:
        matches = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields, fs = left.split(), right.split()
            target = Path(decode(fields[4]))
            if path.is_relative_to(target):
                matches.append((len(target.parts), int(fields[0]), fs[0], decode(fs[1])))
        identity = max(matches)
        if identity[2] not in ('cifs', 'nfs', 'nfs4') or identity[3] != expected_source:
            raise ValueError
        return identity
    except (OSError, ValueError, IndexError):
        raise PersistenceError('nas_mount_unverified', 'backup') from None


def _immutable(metadata):
    return {k: v for k, v in metadata.items() if k not in ('state', 'error', 'nas_verified_at')}


class MountedNasTransfer:
    """One dedicated, pre-existing NAS directory, pinned for each operation.

    ``mount_probe`` is injectable for isolated tests only. Default identity comes
    from the kernel and must exactly match the configured share source. Directory
    descriptors keep writes on the opened filesystem during unmount races.
    """
    def __init__(self, root, expected_source, *, reserve_bytes=100*1024**2,
                 mount_probe=linux_mount_identity, cancelled=lambda: False):
        self.root = Path(root)
        if not self.root.is_absolute() or not isinstance(expected_source, str) or not expected_source:
            raise PersistenceError('invalid_nas_destination', 'backup')
        if type(reserve_bytes) is not int or reserve_bytes < 0:
            raise PersistenceError('invalid_nas_reserve', 'backup')
        self.expected_source, self.reserve_bytes = expected_source, reserve_bytes
        self.probe, self.cancelled = mount_probe, cancelled

    @contextmanager
    def _destination(self):
        fd = None
        try:
            if os.name != 'posix' or self.root.is_symlink() or self.root.resolve() != self.root:
                raise PersistenceError('nas_destination_unavailable', 'backup')
            identity = self.probe(self.root, self.expected_source)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            pinned = os.fstat(fd)

            def check():
                current = self.root.stat()
                if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino) or self.probe(self.root, self.expected_source) != identity:
                    raise PersistenceError('nas_destination_changed', 'backup')
                if self.cancelled():
                    raise PersistenceError('backup_cancelled', 'backup')
            check()
            yield fd, check
            check()
        except OSError:
            raise PersistenceError('nas_io_failed', 'backup') from None
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _file(folder, name, flags):
        fd = os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise PersistenceError('nas_file_unsafe', 'backup')
        return fd

    @staticmethod
    def _sync_directory(fd):
        try:
            os.fsync(fd)
        except OSError as exc:
            # Some network filesystems do not implement directory fsync.
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise

    def _verify(self, parent, name, metadata, check):
        folder = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            with os.fdopen(self._file(folder, 'manifest.json', os.O_RDONLY), 'r', encoding='utf-8') as source:
                raw = source.read(65537)
                if len(raw) > 65536:
                    raise PersistenceError('nas_manifest_invalid', 'backup')
                saved = json.loads(raw)
            if not isinstance(saved, dict) or _immutable(saved) != _immutable(metadata):
                raise PersistenceError('nas_backup_conflict', 'backup')
            digest, size = sha256(), 0
            with os.fdopen(self._file(folder, 'snapshot.sqlite3', os.O_RDONLY), 'rb') as source:
                for block in iter(lambda: source.read(1024*1024), b''):
                    check()
                    size += len(block)
                    if size > metadata['size_bytes']:
                        raise PersistenceError('nas_backup_conflict', 'backup')
                    digest.update(block)
            if size != metadata['size_bytes'] or digest.hexdigest() != metadata['checksum']:
                raise PersistenceError('nas_backup_conflict', 'backup')
            check()
            return TransferReceipt(metadata['id'], digest.hexdigest(), size, True)
        finally:
            os.close(folder)

    def publish(self, source, backup_id, checksum, metadata):
        if not isinstance(backup_id, str) or not _KEY.fullmatch(backup_id) or not isinstance(checksum, str) or not _DIGEST.fullmatch(checksum):
            raise PersistenceError('invalid_backup_id', 'backup')
        if not isinstance(metadata, dict) or set(metadata)-_FIELDS or metadata.get('id') != backup_id or metadata.get('checksum') != checksum or type(metadata.get('size_bytes')) is not int or metadata['size_bytes'] < 1:
            raise PersistenceError('invalid_backup_manifest', 'backup')
        remote = dict(metadata, state='nas_verified', nas_verified_at=datetime.now(timezone.utc).isoformat())
        remote.pop('error', None)
        try:
            with self._destination() as (parent, check):
                try:
                    os.stat(backup_id, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    return self._verify(parent, backup_id, remote, check)
                names = os.listdir(parent)
                if len(names) > 10000 or any(not (_KEY.fullmatch(n) or re.fullmatch(r'\.(upload|delete)-[a-f0-9]{32}', n)) for n in names):
                    raise PersistenceError('nas_area_unmanaged', 'backup')
                if sum(n.startswith('.upload-') for n in names) >= 3:
                    raise PersistenceError('nas_incomplete_limit', 'backup')
                disk = os.fstatvfs(parent)
                if disk.f_bavail*disk.f_frsize < metadata['size_bytes']+self.reserve_bytes:
                    raise PersistenceError('nas_capacity_insufficient', 'backup')
                pending = '.upload-'+uuid4().hex
                os.mkdir(pending, mode=0o700, dir_fd=parent)
                folder = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                promoted = False
                try:
                    digest, size = sha256(), 0
                    with Path(source).open('rb') as inp, os.fdopen(self._file(folder, 'snapshot.sqlite3', os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as out:
                        for block in iter(lambda: inp.read(1024*1024), b''):
                            check()
                            size += len(block)
                            if size > metadata['size_bytes']:
                                raise PersistenceError('backup_checksum_mismatch', 'backup')
                            out.write(block)
                            digest.update(block)
                        out.flush()
                        os.fsync(out.fileno())
                    if size != metadata['size_bytes'] or digest.hexdigest() != checksum:
                        raise PersistenceError('backup_checksum_mismatch', 'backup')
                    with os.fdopen(self._file(folder, 'manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'w', encoding='utf-8') as out:
                        json.dump(remote, out, sort_keys=True)
                        out.flush()
                        os.fsync(out.fileno())
                    self._sync_directory(folder)
                    self._verify(parent, pending, remote, check)
                    # A completed nonempty destination cannot be replaced by rename.
                    os.rename(pending, backup_id, src_dir_fd=parent, dst_dir_fd=parent)
                    promoted = True
                    self._sync_directory(parent)
                    return self._verify(parent, backup_id, remote, check)
                finally:
                    try:
                        if not promoted:
                            # Only this attempt's known files, through its pinned descriptor.
                            for name in ('snapshot.sqlite3', 'manifest.json'):
                                try:
                                    os.unlink(name, dir_fd=folder)
                                except FileNotFoundError:
                                    pass
                            os.rmdir(pending, dir_fd=parent)
                    finally:
                        os.close(folder)
        except (OSError, ValueError, TypeError):
            raise PersistenceError('nas_transfer_failed', 'backup') from None

    def fetch(self, backup_id, metadata, target):
        """Read one verified NAS copy into a new explicit local temporary file."""
        target = Path(target)
        if not isinstance(backup_id, str) or not _KEY.fullmatch(backup_id) or not isinstance(metadata, dict) or metadata.get('id') != backup_id or not isinstance(metadata.get('checksum'), str) or not _DIGEST.fullmatch(metadata['checksum']) or type(metadata.get('size_bytes')) is not int or metadata['size_bytes'] < 1:
            raise PersistenceError('invalid_backup_manifest', 'backup')
        if not target.is_absolute() or target.is_symlink() or target.exists() or target.parent.resolve() != target.parent or target.is_relative_to(self.root):
            raise PersistenceError('unsafe_nas_fetch_target', 'backup')
        created, complete = False, False
        try:
            with self._destination() as (parent, check):
                self._verify(parent, backup_id, metadata, check)
                folder = os.open(backup_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    with target.open('xb') as output:
                        created = True
                        digest, size = sha256(), 0
                        with os.fdopen(self._file(folder, 'snapshot.sqlite3', os.O_RDONLY), 'rb') as source:
                            for block in iter(lambda: source.read(1024*1024), b''):
                                check(); size += len(block)
                                if size > metadata['size_bytes']:
                                    raise PersistenceError('nas_backup_conflict', 'backup')
                                digest.update(block); output.write(block)
                        output.flush(); os.fsync(output.fileno())
                    if size != metadata['size_bytes'] or digest.hexdigest() != metadata['checksum']:
                        raise PersistenceError('nas_backup_conflict', 'backup')
                finally:
                    os.close(folder)
            complete = True
            return TransferReceipt(backup_id, digest.hexdigest(), size, True)
        except (OSError, ValueError, TypeError):
            raise PersistenceError('nas_fetch_failed', 'backup') from None
        finally:
            if created and not complete:
                target.unlink(missing_ok=True)

    def verify(self, metadata):
        key = metadata.get('id') if isinstance(metadata, dict) else None
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise PersistenceError('invalid_backup_id', 'backup')
        try:
            with self._destination() as (parent, check):
                return self._verify(parent, key, metadata, check)
        except (OSError, ValueError, TypeError):
            raise PersistenceError('nas_verification_failed', 'backup') from None

    def retire(self, metadata):
        """Delete only a validated, selected backup via a resumable private name.

        Caller owns the durable retention intent and protects retained copies.
        Never recursively enumerate/delete other folders or unknown files.
        """
        key = metadata.get('id') if isinstance(metadata, dict) else None
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise PersistenceError('invalid_backup_id', 'backup')
        pending = '.delete-'+key
        try:
            with self._destination() as (parent, check):
                names = os.listdir(parent)
                if key in names and pending in names:
                    raise PersistenceError('nas_backup_conflict', 'backup')
                if key in names:
                    self._verify(parent, key, metadata, check)
                    folder = os.open(key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        if set(os.listdir(folder)) != {'snapshot.sqlite3', 'manifest.json'}:
                            raise PersistenceError('nas_area_unmanaged', 'backup')
                    finally:
                        os.close(folder)
                    check()
                    os.rename(key, pending, src_dir_fd=parent, dst_dir_fd=parent)
                    self._sync_directory(parent)
                elif pending not in names:
                    return  # retry after deletion, before local receipt was updated
                folder = os.open(pending, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    names = set(os.listdir(folder))
                    if names-{'snapshot.sqlite3', 'manifest.json'} or ('snapshot.sqlite3' in names and 'manifest.json' not in names):
                        raise PersistenceError('nas_area_unmanaged', 'backup')
                    if 'manifest.json' in names:
                        with os.fdopen(self._file(folder, 'manifest.json', os.O_RDONLY), 'r', encoding='utf-8') as source:
                            raw = source.read(65537)
                        saved = json.loads(raw) if len(raw) <= 65536 else None
                        if not isinstance(saved, dict) or _immutable(saved) != _immutable(metadata):
                            raise PersistenceError('nas_backup_conflict', 'backup')
                    if 'snapshot.sqlite3' in names:
                        # A complete quarantined copy is verified before removal,
                        # including when resuming after a process restart.
                        self._verify(parent, pending, metadata, check)
                        check(); os.unlink('snapshot.sqlite3', dir_fd=folder)
                    if 'manifest.json' in names:
                        check(); os.unlink('manifest.json', dir_fd=folder)
                    check(); os.rmdir(pending, dir_fd=parent)
                    self._sync_directory(parent)
                finally:
                    os.close(folder)
        except (OSError, ValueError, TypeError):
            raise PersistenceError('nas_retirement_failed', 'backup') from None
