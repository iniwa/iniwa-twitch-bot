"""Connect durable requests and stream completions to the backup coordinator."""

from copy import deepcopy
from hashlib import sha256
from threading import RLock

from .backup_coordinator import BackupCoordinator
from .persistence import PersistenceError


class BackupWorker:
    def __init__(self, service, repository, *, running=lambda: False):
        self.service, self.repository, self.running = service, repository, running
        self.coordinator = BackupCoordinator(service, running=running)
        self.lock = RLock()
        self._status = {'state': 'not_started', 'items': [], 'nas_configured': service.transfer is not None}
        self.next_retention = None

    def snapshot(self):
        with self.lock:
            return deepcopy(self._status)

    def step(self):
        if not self.running():
            return {'state': 'paused'}
        try:
            restore = self.repository.claim_restore()
            if restore:
                try:
                    area = self.service.database.path.parent/'restore-candidates'
                    if area.is_symlink() or area.resolve() != area:
                        raise PersistenceError('unsafe_restore_area', 'backup')
                    area.mkdir(exist_ok=True)
                    name = sha256(restore['id'].encode()).hexdigest()+'.sqlite3'
                    self.service.retrieve(restore['backup_id'])
                    if not self.running():
                        raise PersistenceError('backup_cancelled', 'backup')
                    self.service.prepare_restore(restore['backup_id'], area/name)
                    self.repository.finish_restore(restore['id'], candidate_name=name)
                except (PersistenceError, OSError) as exc:
                    self.repository.finish_restore(restore['id'], code=exc.code if isinstance(exc, PersistenceError) else 'restore_area_unavailable')
            job = self.repository.claim()
            if job:
                try:
                    item = self.service.create(cancelled=lambda: not self.running())
                    self.repository.finish(job['id'], backup_id=item['id'])
                except PersistenceError as exc:
                    self.repository.finish(job['id'], code=exc.code, state='failed')
            policy = self.repository.policy()
            items = self.service.list_backups()
            covered = {sid for item in items for sid in item.get('stream_ids', [])}
            if policy['enabled']:
                result = self.coordinator.step(self.repository.completed_streams(covered), daily_hour=policy['daily_hour'])
                now = self.service.clock()
                if self.running() and (self.next_retention is None or now >= self.next_retention):
                    from datetime import timedelta
                    self.next_retention = now+timedelta(minutes=5)
                    protected = lambda: [job['backup_id'] for job in self.repository.restore_jobs() if job['state'] in ('pending','running')]
                    self.service.maintain_retention(protected_ids=protected)
                    if self.running():
                        self.service.compact_local(protected_ids=protected)
            else:
                result = {'state': 'automatic_paused'}
                # Explicit manual requests still transfer while automatic creation is paused.
                pending = [item for item in reversed(items) if item['state'] in ('local_ready', 'transfer_failed')]
                now = self.service.clock()
                if pending and self.service.transfer and (self.coordinator.retry_after is None or now >= self.coordinator.retry_after):
                    try:
                        self.service.publish(pending[0]['id'])
                    except PersistenceError:
                        from datetime import timedelta
                        self.coordinator.retry_after = now+timedelta(minutes=5)
                        raise
            # New daily/stream-end copies transfer on the next scheduler step.
            state = {**result, 'items': self.service.list_backups(), 'nas_configured': self.service.transfer is not None}
        except PersistenceError as exc:
            state = {**self.snapshot(), 'state': 'degraded', 'error': exc.code}
        with self.lock:
            self._status = state
        return {k: v for k, v in state.items() if k != 'items'}
