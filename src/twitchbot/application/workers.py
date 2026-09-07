"""Explicit worker lifecycle with cached status and process-owned writer lease."""

from copy import deepcopy
import os
from pathlib import Path
from threading import Event, RLock, Thread

from ..runtime import RuntimeSupervisor
from .persistence import PersistenceError


class ProcessLease:
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def acquire(self):
        if self.file is not None:
            return
        if not self.path.is_absolute() or self.path.is_symlink() or self.path.parent.resolve() != self.path.parent:
            raise PersistenceError('unsafe_runtime_lease', 'runtime')
        stream = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if stream.seek(0, 2) == 0:
                    stream.write(b'0'); stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise PersistenceError('runtime_already_owned', 'runtime') from None
        self.file = stream

    def release(self):
        if self.file is not None:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None


class PeriodicWorker:
    def __init__(self, name, step, *, interval=20):
        if interval <= 0:
            raise ValueError('invalid interval')
        self.name, self.step, self.interval = name, step, interval
        self._stop, self._wake, self._lock = Event(), Event(), RLock()
        self._thread, self._status = None, {'state': 'stopped'}

    def snapshot(self):
        with self._lock:
            return deepcopy(self._status)

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name=self.name, daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                result = self.step()
                state = {'state': 'running', 'result': result}
            except Exception as exc:
                code = exc.code if isinstance(exc, PersistenceError) else 'worker_step_failed'
                state = {'state': 'degraded', 'error': code}
            with self._lock:
                self._status = state
            self._wake.wait(self.interval)
        with self._lock:
            self._status = {'state': 'stopped'}

    def request_stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self):
        self._wake.set()

    def join(self, timeout=10):
        if self._thread:
            self._thread.join(timeout)
        return self._thread is None or not self._thread.is_alive()


class WorkerRuntime(RuntimeSupervisor):
    def __init__(self, lease, workers=(), *, recover=(), stopped=()):
        super().__init__()
        self.lease, self.workers = lease, tuple(workers)
        self.recover, self.stopped = tuple(recover), tuple(stopped)
        self._stopping = False
        self._lifecycle = RLock()

    def start(self):
        # Workers may read snapshot() during startup and rollback. Never join
        # them while holding the snapshot lock.
        with self._lifecycle:
            if self._stopping:
                raise PersistenceError('runtime_still_stopping', 'runtime')
            if self.snapshot().ready:
                return self.snapshot()
            self.lease.acquire()
            try:
                for recover in self.recover:
                    recover()
                super().start()
                for worker in self.workers:
                    worker.start()
            except Exception:
                self.stop()
                raise
            return self.snapshot()

    def stop(self):
        with self._lifecycle:
            return self._stop_workers()

    def _stop_workers(self):
        # Close the dispatch gate before joining network/storage workers.
        with self._lock:
            super().stop()
            self._stopping = True
            for worker in self.workers:
                worker.request_stop()
        joined = [worker.join() for worker in self.workers]
        if all(joined):
            try:
                for stopped in self.stopped:
                    stopped()
            finally:
                self.lease.release()
                self._stopping = False
        return self.snapshot()
