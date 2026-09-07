"""Operator state changes; background threads are owned by process startup."""

from dataclasses import replace
from threading import Lock, RLock

from .persistence import PersistenceError


class Operations:
    def __init__(self, runtime, settings_repository, maintenance, backup_worker, client, recorder):
        self.runtime, self.settings_repository, self.maintenance = runtime, settings_repository, maintenance
        self.backup_worker, self.client, self.recorder = backup_worker, client, recorder
        self.lock = RLock()
        self.transition = Lock()
        self.settings = settings_repository.load()
        self.paused = ()

    def allowed(self):
        with self.lock:
            return self.settings.settings.bot_enabled and self.runtime.snapshot().ready

    def snapshot(self):
        with self.lock:
            enabled, revision = self.settings.settings.bot_enabled, self.settings.revision
        # Helix calls consult allowed() while holding their own lock. Never hold
        # the settings lock while inspecting Helix (or another worker), or the
        # status page and a recording request can wait for each other forever.
        return {'enabled': enabled, 'revision': revision,
                'runtime': self.runtime.snapshot().as_dict(), 'connection': self.client.status(),
                'workers': {worker.name: worker.snapshot() for worker in self.runtime.workers},
                'backup_policy': self.maintenance.policy(), 'backups': self.backup_worker.snapshot(),
                'jobs': self.maintenance.jobs(), 'restore_jobs': self.maintenance.restore_jobs()}

    def set_enabled(self, enabled, revision):
        if type(enabled) is not bool or type(revision) is not int or revision < 0:
            raise PersistenceError('invalid_runtime_setting', 'runtime')
        with self.transition:
            current = self.settings_repository.load()
            saved = self.settings_repository.save(replace(current.settings, bot_enabled=enabled), revision)
            if enabled and not current.settings.bot_enabled:
                self.recorder.resume()
            with self.lock:
                self.settings = saved
            if not enabled:
                self.recorder.stopped()
                for callback in self.paused:
                    callback()
            for worker in self.runtime.workers:
                worker.wake()
        return {'enabled': enabled, 'revision': saved.revision}

    def request_backup(self, request_id):
        result = self.maintenance.enqueue(request_id)
        for worker in self.runtime.workers:
            if worker.name == 'backups':
                worker.wake()
        return result

    def update_backup_policy(self, enabled, hour, revision):
        result = self.maintenance.save_policy(enabled, hour, revision)
        for worker in self.runtime.workers:
            if worker.name == 'backups':
                worker.wake()
        return result

    def request_restore(self, request_id, backup_id):
        result = self.backup_worker.service.reserve_restore(
            backup_id, lambda: self.maintenance.enqueue_restore(request_id, backup_id))
        for worker in self.runtime.workers:
            if worker.name == 'backups': worker.wake()
        return result
