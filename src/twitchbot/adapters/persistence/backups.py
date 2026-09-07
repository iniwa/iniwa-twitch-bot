"""Explicit local snapshots and restore candidates. Never replace the live DB."""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from copy import deepcopy
from threading import Lock
from uuid import uuid4

from ...application.backups import TransferReceipt
from ...application.backups import daily_backup_due
from ...application.backups import retention_candidates
from ...application.analytics import identifier
from ...application.persistence import PersistenceError
from ...application.workers import ProcessLease
from ...settings import AppSettings
from .migrations import MIGRATIONS
from .repositories import SettingsRepository
from .sqlite import SQLiteDatabase, from_rfc3339, to_rfc3339, utc_now, validate_database_path


@dataclass(frozen=True, slots=True)
class BackupLimits:
    max_pending: int = 3
    max_bytes: int = 10 * 1024**3
    reserve_bytes: int = 5 * 1024**3
    reserve_fraction: float = .1

    def __post_init__(self):
        if type(self.max_pending) is not int or self.max_pending<1 or type(self.max_bytes) is not int or self.max_bytes<1 or type(self.reserve_bytes) is not int or self.reserve_bytes<0 or not 0<=self.reserve_fraction<1:
            raise PersistenceError("invalid_backup_limits", "backup")


def checksum(path):
    digest=sha256()
    with path.open("rb") as source:
        for block in iter(lambda:source.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


class BackupService:
    def __init__(self, database, staging_root, *, limits=None, transfer=None, clock=utc_now):
        root=Path(staging_root)
        if not root.is_absolute():
            raise PersistenceError("invalid_backup_path", "backup")
        self.database,self.root=database,root
        self.limits=limits or BackupLimits()
        self.transfer=transfer
        self.clock=clock
        self._lock=Lock()

    def _acquire(self):
        if not self._lock.acquire(blocking=False):
            raise PersistenceError("backup_busy", "backup")
        try:
            self._area()
            self._lease=ProcessLease(self.root/".backup.lock")
            self._lease.acquire()
        except OSError:
            self._lock.release()
            raise PersistenceError("backup_area_unavailable", "backup") from None
        except PersistenceError as exc:
            self._lock.release()
            if exc.code == 'runtime_already_owned':
                raise PersistenceError('backup_busy', 'backup') from None
            raise
        except Exception:
            self._lock.release()
            raise

    def _release(self):
        try:
            self._lease.release()
        except OSError:
            raise PersistenceError("backup_lock_release_failed", "backup") from None
        finally:
            self._lock.release()

    @staticmethod
    def _verify_schema(c, *, allow_older=False):
        history=[tuple(r) for r in c.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version")]
        migrations = MIGRATIONS[:len(history)] if allow_older and 1 <= len(history) <= len(MIGRATIONS) else MIGRATIONS
        if history!=[(m.version,m.name,m.checksum) for m in migrations]:
            raise PersistenceError("backup_schema_mismatch", "backup")
        expected={}
        for migration in migrations:
            for sql in migration.statements:
                match=re.match(r"CREATE (TABLE|INDEX|TRIGGER) (\S+)",sql)
                if match:
                    expected[(match[1].lower(),match[2])]=sql
        actual={(r[0],r[1]):r[2] for r in c.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL")}
        if actual!=expected:
            raise PersistenceError("backup_schema_mismatch", "backup")
        if c.execute("PRAGMA quick_check").fetchone()[0]!="ok" or c.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PersistenceError("backup_integrity_failed", "backup")
        # Unknown/credential-like settings are rejected before copying out.
        c.row_factory=sqlite3.Row
        SettingsRepository._parse(c.execute("SELECT * FROM settings").fetchall())
        tables=[name for kind,name in expected if kind=="table"]
        return {name:c.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in tables}

    def _area(self):
        if not self.root.is_dir() or self.root.is_symlink() or self.root.resolve()!=self.root:
            raise PersistenceError("backup_area_unavailable", "backup")

    def _folder(self, key):
        if not isinstance(key,str) or re.fullmatch(r"[a-f0-9]{32}",key) is None:
            raise PersistenceError("invalid_backup_id", "backup")
        self._area()
        folder=self.root/key
        if folder.is_symlink() or folder.resolve().parent!=self.root.resolve():
            raise PersistenceError("invalid_backup_path", "backup")
        return folder

    @staticmethod
    def _write_manifest(folder, manifest):
        temporary=folder/("manifest.pending-"+uuid4().hex+".json")
        try:
            with temporary.open("x",encoding="utf-8") as stream:
                json.dump(manifest,stream,ensure_ascii=True,sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(folder/"manifest.json")
        finally:
            temporary.unlink(missing_ok=True)
        if os.name == 'posix':
            descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def list_backups(self, *, sort="created_at", order="desc", state=None):
        if sort not in ("created_at", "size_bytes") or order not in ("asc", "desc"):
            raise PersistenceError("invalid_sort", "backup")
        if state is not None and state not in ("local_ready", "transfer_failed", "nas_verified", "retiring", "expired"):
            raise PersistenceError("invalid_filter", "backup")
        try:
            items = self._list_backups()
        except OSError:
            raise PersistenceError("backup_area_unavailable", "backup") from None
        if state is not None:
            items = [item for item in items if item["state"] == state]
        return sorted(items, key=lambda item: (item[sort], item["id"]), reverse=order == "desc")

    def _list_backups(self):
        self._area()
        items=[]
        for folder in self.root.iterdir():
            if folder.name==".backup.lock" and folder.is_file() and not folder.is_symlink():
                continue
            if not folder.is_dir() or folder.is_symlink() or re.fullmatch(r"[a-f0-9]{32}",folder.name) is None:
                raise PersistenceError("backup_area_unmanaged", "backup")
            manifest=folder/"manifest.json"
            if manifest.is_file() and not manifest.is_symlink():
                if manifest.stat().st_size>64*1024:
                    raise PersistenceError("invalid_backup_manifest", "backup")
                try:
                    data=json.loads(manifest.read_text(encoding="utf-8"))
                    if not isinstance(data,dict) or data.get("id")!=folder.name or data["state"] not in ("local_ready","transfer_failed","nas_verified","retiring","expired") or type(data["size_bytes"]) is not int or data["size_bytes"]<1 or re.fullmatch(r"[a-f0-9]{64}",data["checksum"]) is None or not isinstance(data["counts"],dict) or any(type(n) is not int or n<0 for n in data["counts"].values()):
                        raise ValueError
                    from_rfc3339(data["created_at"])
                    from_rfc3339(data["copy_completed_at"])
                    if not isinstance(data["reasons"],list) or not data["reasons"] or any(reason not in ("manual","daily","stream_end","before_change") for reason in data["reasons"]):
                        raise ValueError
                    if data["state"]=="nas_verified":
                        from_rfc3339(data["nas_verified_at"])
                except (KeyError,TypeError,ValueError,PersistenceError):
                    raise PersistenceError("invalid_backup_manifest", "backup") from None
                items.append(data)
        return items

    def _capacity(self, estimate, *, creating=True):
        items=self.list_backups()
        if creating and sum(i["state"] in ("local_ready","transfer_failed") for i in items)>=self.limits.max_pending:
            raise PersistenceError("backup_queue_full", "backup")
        used=0
        for folder in self.root.iterdir():
            if folder.name==".backup.lock":
                continue
            for path in folder.iterdir():
                if path.is_symlink() or not path.is_file():
                    raise PersistenceError("backup_area_unmanaged", "backup")
                used+=path.stat().st_size
        disk=shutil.disk_usage(self.root)
        reserve=max(self.limits.reserve_bytes,int(disk.total*self.limits.reserve_fraction))
        if used+estimate>self.limits.max_bytes or disk.free-estimate<reserve:
            raise PersistenceError("backup_capacity_insufficient", "backup")

    def create(self, *, reasons=("manual",), stream_ids=(), cancelled=lambda:False, daily_hour=4):
        daily_backup_due(self.clock(), None, running=True, hour=daily_hour)
        if not isinstance(reasons,tuple) or not reasons or any(r not in ("manual","daily","stream_end","before_change") for r in reasons):
            raise PersistenceError("invalid_backup_reason", "backup")
        if not isinstance(stream_ids,tuple) or len(stream_ids)>1000:
            raise PersistenceError("invalid_backup_streams", "backup")
        for stream_id in stream_ids:
            identifier(stream_id)
        self._acquire()
        folder=None
        try:
            self._area()
            created=to_rfc3339(self.clock())
            source=sqlite3.connect(self.database.path.as_uri()+"?mode=ro",uri=True)
            try:
                source.row_factory=sqlite3.Row
                source.execute("BEGIN")
                self._verify_schema(source)
                estimate=source.execute("PRAGMA page_count").fetchone()[0]*source.execute("PRAGMA page_size").fetchone()[0]
                self._capacity(estimate*2)
                key=uuid4().hex
                folder=self._folder(key)
                folder.mkdir(exist_ok=False)
                destination=sqlite3.connect(folder/"snapshot.sqlite3")
                try:
                    def progress(status,remaining,total):
                        if cancelled():
                            raise PersistenceError("backup_cancelled", "backup")
                        self._capacity(remaining*source.execute("PRAGMA page_size").fetchone()[0],creating=False)
                    source.backup(destination,pages=256,progress=progress,sleep=.01)
                    counts=self._verify_schema(destination)
                    destination.execute("PRAGMA journal_mode=DELETE")
                finally:
                    destination.close()
            finally:
                source.close()
            path=folder/"snapshot.sqlite3"
            manifest={"id":key,"state":"local_ready","reasons":list(dict.fromkeys(reasons)),"created_at":created,
                      "copy_completed_at":to_rfc3339(self.clock()),"schema_version":len(MIGRATIONS),"size_bytes":path.stat().st_size,
                      "checksum":checksum(path),"counts":counts,"nas_verified_at":None}
            manifest["stream_ids"]=list(dict.fromkeys(stream_ids))
            manifest["daily_day"]=daily_backup_due(from_rfc3339(created),None,running=True,hour=daily_hour) if "daily" in reasons else None
            self._write_manifest(folder,manifest)
            return manifest
        except (OSError,sqlite3.Error,ValueError) as error:
            raise PersistenceError("backup_failed", "backup") from error
        finally:
            # Remove only this call's incomplete, newly-created files. Never a prior copy.
            if folder is not None and not (folder/"manifest.json").exists():
                for name in ("snapshot.sqlite3","snapshot.sqlite3-journal","snapshot.sqlite3-wal","snapshot.sqlite3-shm","manifest.pending.json"):
                    path=folder/name
                    if path.is_file() and not path.is_symlink():path.unlink()
                if folder.exists() and not any(folder.iterdir()):folder.rmdir()
            self._release()

    def verify(self, key):
        try:
            return self._verify(key)
        except (OSError,sqlite3.Error):
            raise PersistenceError("backup_verification_failed", "backup") from None

    def _verify(self, key):
        folder=self._folder(key)
        items=[item for item in self.list_backups() if item["id"]==key]
        if not items:
            raise PersistenceError("backup_not_found", "backup")
        manifest=items[0]
        if manifest['state'] in ('retiring', 'expired'):
            raise PersistenceError('backup_retired', 'backup')
        path=folder/"snapshot.sqlite3"
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=manifest["size_bytes"] or checksum(path)!=manifest["checksum"]:
            raise PersistenceError("backup_checksum_mismatch", "backup")
        c=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
        try:
            counts=self._verify_schema(c, allow_older=True)
            if counts!=manifest["counts"]:
                raise PersistenceError("backup_counts_mismatch", "backup")
            if type(manifest.get('schema_version')) is not int or manifest['schema_version'] != counts['schema_migrations']:
                raise PersistenceError('backup_schema_mismatch', 'backup')
        finally:c.close()
        return manifest

    def publish(self, key):
        self._acquire()
        try:
            manifest=self.verify(key)
            if manifest["state"]=="nas_verified":return manifest
            if self.transfer is None:
                raise PersistenceError("nas_transfer_unavailable", "backup")
            folder=self._folder(key)
            try:
                receipt=self.transfer.publish(folder/"snapshot.sqlite3",key,manifest["checksum"],deepcopy(manifest))
                if not isinstance(receipt,TransferReceipt) or receipt.destination_verified is not True or type(receipt.size_bytes) is not int or (receipt.backup_id,receipt.checksum,receipt.size_bytes)!=(key,manifest["checksum"],manifest["size_bytes"]):
                    raise PersistenceError("nas_verification_failed", "backup")
            except Exception:
                manifest.update(state="transfer_failed",error="nas_verification_failed")
                self._write_manifest(folder,manifest)
                raise PersistenceError("nas_verification_failed", "backup") from None
            manifest.update(state="nas_verified",nas_verified_at=to_rfc3339(self.clock()))
            manifest.pop("error",None)
            self._write_manifest(folder,manifest)
            return manifest
        finally:self._release()

    def prepare_restore(self, key, candidate_path):
        """Create a new stopped candidate; never switch, overwrite or merge live data."""
        self._acquire()
        try:
            candidate=validate_database_path(candidate_path)
            manifest=self.verify(key)
            if candidate.exists() or candidate.is_symlink() or candidate.resolve()==self.database.path.resolve() or candidate.parent.resolve()!=candidate.parent:
                raise PersistenceError("restore_target_exists_or_unsafe", "backup")
            disk=shutil.disk_usage(candidate.parent)
            reserve=max(self.limits.reserve_bytes,int(disk.total*self.limits.reserve_fraction))
            if disk.free-manifest["size_bytes"]*2<reserve:
                raise PersistenceError("backup_capacity_insufficient", "backup")
            with candidate.open("xb"):pass
            source=sqlite3.connect((self._folder(key)/"snapshot.sqlite3").as_uri()+"?mode=ro",uri=True)
            destination=None
            try:
                destination=sqlite3.connect(candidate)
                source.backup(destination)
                destination.row_factory=sqlite3.Row
                self._verify_schema(destination, allow_older=True)
                destination.close()
                destination=None
                SQLiteDatabase(candidate).migrate()
                destination=sqlite3.connect(candidate)
                destination.row_factory=sqlite3.Row
                self._verify_schema(destination)
                now=to_rfc3339(self.clock())
                current=SettingsRepository._parse(destination.execute("SELECT * FROM settings").fetchall())
                values=current.settings.to_mapping()
                values.update(bot_enabled=False,welcome_enabled=False,enable_vod_download=False,ignore_stream_status=False)
                stopped=AppSettings.from_mapping(values)
                destination.execute("BEGIN IMMEDIATE")
                destination.execute("DELETE FROM settings")
                destination.executemany("INSERT INTO settings VALUES (?,?,?,?)",[(key,json.dumps(value),current.revision+1,now) for key,value in stopped.to_mapping().items()])
                destination.execute("DELETE FROM channel_read_model")
                destination.execute("UPDATE control_operations SET state='unknown',result_code='restore_requires_review',finished_at=? WHERE state IN ('pending','dispatching')",(now,))
                destination.execute("UPDATE follower_sync_runs SET state='failed',finished_at=? WHERE state='collecting'",(now,))
                destination.execute("DELETE FROM preset_previews")
                destination.execute("UPDATE chat_body_deletions SET expires_at=? WHERE state='preview'",(now,))
                destination.execute("UPDATE community_state SET revision=revision+1,follow_revision=follow_revision+1")
                destination.execute("UPDATE backup_policy SET enabled=0,revision=revision+1")
                destination.execute("UPDATE automation_policy SET commands_enabled=0,posts_enabled=0,revision=revision+1")
                destination.execute("UPDATE automation_definitions SET enabled=0,revision=revision+1,execution_revision=execution_revision+1")
                destination.execute("UPDATE chat_dispatches SET state='unknown',reason='restore_requires_review',finished_at=? WHERE state IN ('pending','dispatching')",(now,))
                destination.execute("DELETE FROM post_waits")
                destination.execute("UPDATE prediction_policy SET enabled=0,revision=revision+1")
                destination.execute("UPDATE prediction_operations SET state=CASE WHEN state='dispatching' THEN 'unknown' ELSE 'expired' END,result_code='restore_requires_review',finished_at=? WHERE state IN ('preview','pending','dispatching')",(now,))
                destination.execute("UPDATE restore_jobs SET state='unknown',result_code='restore_requires_review',finished_at=? WHERE state IN ('pending','running')",(now,))
                destination.execute("UPDATE backup_jobs SET state='unknown',result_code='restore_requires_review',finished_at=? WHERE state IN ('pending','running')",(now,))
                destination.commit()
                destination.execute("PRAGMA journal_mode=DELETE")
                counts=self._verify_schema(destination)
            finally:
                if destination is not None:destination.close()
                source.close()
            return {"backup_id":key,"state":"candidate_verified","counts":counts,"automatic_execution":"stopped","chat_bodies_may_return":True}
        except (OSError,sqlite3.Error,ValueError) as error:
            raise PersistenceError("restore_candidate_failed", "backup") from error
        finally:
            # A failed candidate remains at its explicit path for inspection, never active.
            self._release()

    def retrieve(self, key):
        """Explicitly retrieve a missing local payload; never run from a GET."""
        self._acquire()
        pending = None
        try:
            folder = self._folder(key)
            if (folder/'snapshot.sqlite3').exists():
                return self.verify(key)
            items = [m for m in self.list_backups() if m['id'] == key]
            if not items or items[0]['state'] != 'nas_verified' or self.transfer is None or not hasattr(self.transfer, 'fetch'):
                raise PersistenceError('nas_retrieval_unavailable', 'backup')
            manifest = items[0]
            self._capacity(manifest['size_bytes'], creating=False)
            pending = folder/('download-'+uuid4().hex+'.pending')
            receipt = self.transfer.fetch(key, deepcopy(manifest), pending)
            if not isinstance(receipt, TransferReceipt) or receipt.destination_verified is not True or (receipt.backup_id,receipt.checksum,receipt.size_bytes) != (key,manifest['checksum'],manifest['size_bytes']):
                raise PersistenceError('nas_verification_failed', 'backup')
            candidate = sqlite3.connect(pending.as_uri()+'?mode=ro',uri=True)
            try:
                if self._verify_schema(candidate, allow_older=True) != manifest['counts']:
                    raise PersistenceError('backup_counts_mismatch', 'backup')
            finally:
                candidate.close()
            # Create without overwriting any file that appeared in the meantime.
            os.link(pending, folder/'snapshot.sqlite3')
            return self.verify(key)
        except OSError:
            raise PersistenceError('nas_retrieval_failed', 'backup') from None
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
            self._release()

    def reserve_restore(self, key, enqueue):
        """Serialize request acceptance with retirement, without reading the NAS."""
        self._acquire()
        try:
            self._folder(key)
            item = next((m for m in self.list_backups() if m['id'] == key), None)
            if item is None or item['state'] in ('retiring', 'expired'):
                raise PersistenceError('backup_retired_or_missing', 'backup')
            return enqueue()
        finally:
            self._release()

    def compact_local(self, *, protected_ids=()):
        """Release old local payloads only after checking their retained NAS copy.

        Keep the newest NAS-verified payload locally, all manual/pre-change
        copies, pending transfers and accepted restores. Missing payloads remain
        nas_verified and are retrieved explicitly when a restore is requested.
        The shared ownership covers verification and removal; presence is the
        restart-safe record of whether an individual payload was released.
        """
        if self.transfer is None or not all(hasattr(self.transfer, name) for name in ('verify', 'fetch')):
            return ()
        self._acquire()
        try:
            items = [item for item in self.list_backups() if item['state'] == 'nas_verified']
            if len(items) < 2:
                return ()
            protected = set(protected_ids() if callable(protected_ids) else protected_ids)
            protected.add(items[0]['id'])
            protected.update(m['id'] for m in items if set(m['reasons']) & {'manual', 'before_change'})
            # If the newest local copy is absent or corrupt, release nothing.
            self.verify(items[0]['id'])
            self._verify_remote(items[0])
            released = []
            for item in items:
                if item['id'] in protected:
                    continue
                folder = self._folder(item['id'])
                payload = folder/'snapshot.sqlite3'
                if payload.is_symlink():
                    raise PersistenceError('invalid_backup_path', 'backup')
                if not payload.exists():
                    continue
                self.verify(item['id'])
                self._verify_remote(item)
                payload.unlink()
                if os.name == 'posix':
                    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try: os.fsync(descriptor)
                    finally: os.close(descriptor)
                released.append(item['id'])
            return tuple(released)
        except OSError:
            raise PersistenceError('backup_compaction_failed', 'backup') from None
        finally:
            self._release()

    def _verify_remote(self, manifest):
        receipt = self.transfer.verify(deepcopy(manifest))
        if not isinstance(receipt, TransferReceipt) or receipt.destination_verified is not True or (receipt.backup_id, receipt.checksum, receipt.size_bytes) != (manifest['id'], manifest['checksum'], manifest['size_bytes']):
            raise PersistenceError('nas_verification_failed', 'backup')

    def maintain_retention(self, *, protected_ids=()):
        """Apply the accepted retention policy after verifying the newest NAS copy.

        Keep metadata for retired copies so old days/streams are not requeued.
        Pending restores and manual/before-change copies are never candidates.
        """
        if self.transfer is None or not hasattr(self.transfer, 'retire') or not hasattr(self.transfer, 'verify'):
            return ()
        self._acquire()
        try:
            items = self.list_backups()
            successful = [item for item in items if item['state'] == 'nas_verified']
            if len(successful) < 2:
                return ()
            candidates = set(retention_candidates(items)) | {m['id'] for m in items if m['state'] == 'retiring'}
            candidates -= set(protected_ids() if callable(protected_ids) else protected_ids)
            candidates -= {m['id'] for m in items if set(m['reasons']) & {'manual', 'before_change'}}
            candidates.discard(successful[0]['id'])
            if not candidates:
                return ()
            newest = successful[0]
            receipt = self.transfer.verify(deepcopy(newest))
            if not isinstance(receipt, TransferReceipt) or receipt.destination_verified is not True or (receipt.backup_id, receipt.checksum, receipt.size_bytes) != (newest['id'], newest['checksum'], newest['size_bytes']):
                raise PersistenceError('nas_verification_failed', 'backup')
            retired = []
            for manifest in items:
                if manifest['id'] not in candidates:
                    continue
                folder = self._folder(manifest['id'])
                if manifest['state'] != 'retiring':
                    if (folder/'snapshot.sqlite3').exists() or (folder/'snapshot.sqlite3').is_symlink():
                        self.verify(manifest['id'])
                    else:
                        # Retained NAS generations may have released their local
                        # payload. Validate that remote copy before retirement.
                        self._verify_remote(manifest)
                    manifest = dict(manifest, state='retiring')
                    self._write_manifest(folder, manifest)
                self.transfer.retire(deepcopy(manifest))
                payload = folder/'snapshot.sqlite3'
                if payload.is_symlink():
                    raise PersistenceError('invalid_backup_path', 'backup')
                payload.unlink(missing_ok=True)
                manifest = dict(manifest, state='expired')
                self._write_manifest(folder, manifest)
                retired.append(manifest['id'])
            return tuple(retired)
        except OSError:
            raise PersistenceError('backup_retirement_failed', 'backup') from None
        finally:
            self._release()
