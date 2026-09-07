"""Durable backup requests and scheduling preferences, with read-only queries."""

from ...application.analytics import identifier
from ...application.persistence import PersistenceError, RevisionConflictError
from .community import CommunityRepository
from .sqlite import to_rfc3339


class MaintenanceRepository:
    def __init__(self, database, channel_id):
        self.records = CommunityRepository(database, channel_id)

    def policy(self):
        with self.records.transaction() as c:
            row = c.execute('SELECT enabled,daily_hour,revision FROM backup_policy WHERE id=1').fetchone()
        return {'enabled': bool(row['enabled']), 'daily_hour': row['daily_hour'], 'revision': row['revision']} if row else {'enabled': False, 'daily_hour': 4, 'revision': 0}

    def save_policy(self, enabled, daily_hour, revision):
        if type(enabled) is not bool or type(daily_hour) is not int or not 0 <= daily_hour <= 23 or type(revision) is not int or revision < 0:
            raise PersistenceError('invalid_backup_policy', 'backup')
        with self.records.transaction(write=True) as c:
            row = c.execute('SELECT revision FROM backup_policy WHERE id=1').fetchone()
            if (row['revision'] if row else 0) != revision:
                raise RevisionConflictError('backup_policy')
            c.execute('INSERT INTO backup_policy VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,daily_hour=excluded.daily_hour,revision=excluded.revision',
                      (int(enabled), daily_hour, revision+1))
        return {'enabled': enabled, 'daily_hour': daily_hour, 'revision': revision+1}

    def enqueue(self, request_id):
        identifier(request_id)
        with self.records.transaction(write=True) as c:
            row = c.execute('SELECT * FROM backup_jobs WHERE id=?', (request_id,)).fetchone()
            if row is None:
                if c.execute("SELECT COUNT(*) FROM backup_jobs WHERE state IN ('pending','running')").fetchone()[0] >= 3:
                    raise PersistenceError('backup_queue_full', 'backup')
                c.execute("INSERT INTO backup_jobs VALUES (?,'pending',NULL,'queued',?,NULL)",
                          (request_id, to_rfc3339(self.records.clock())))
                row = c.execute('SELECT * FROM backup_jobs WHERE id=?', (request_id,)).fetchone()
        return dict(row)

    def jobs(self):
        with self.records.transaction() as c:
            return [dict(r) for r in c.execute('SELECT * FROM backup_jobs ORDER BY created_at DESC,id DESC LIMIT 50')]

    def claim(self):
        with self.records.transaction(write=True) as c:
            row = c.execute("SELECT * FROM backup_jobs WHERE state='pending' ORDER BY created_at,id LIMIT 1").fetchone()
            if row:
                c.execute("UPDATE backup_jobs SET state='running',result_code='creating' WHERE id=?", (row['id'],))
        return dict(row) if row else None

    def finish(self, request_id, *, backup_id=None, code='local_ready', state='succeeded'):
        if state not in ('succeeded', 'failed'):
            raise PersistenceError('invalid_job_state', 'backup')
        with self.records.transaction(write=True) as c:
            c.execute("UPDATE backup_jobs SET state=?,backup_id=?,result_code=?,finished_at=? WHERE id=? AND state='running'",
                      (state, backup_id, code, to_rfc3339(self.records.clock()), request_id))

    def recover(self):
        with self.records.transaction(write=True) as c:
            c.execute("UPDATE restore_jobs SET state='unknown',result_code='interrupted',finished_at=? WHERE state='running'", (to_rfc3339(self.records.clock()),))
            return c.execute("UPDATE backup_jobs SET state='unknown',result_code='interrupted',finished_at=? WHERE state='running'",
                             (to_rfc3339(self.records.clock()),)).rowcount

    def restore_jobs(self):
        with self.records.transaction() as c:
            return [dict(row) for row in c.execute('SELECT * FROM restore_jobs ORDER BY created_at DESC,id DESC LIMIT 30')]

    def enqueue_restore(self, request_id, backup_id):
        from re import fullmatch
        identifier(request_id)
        if not isinstance(backup_id, str) or fullmatch('[a-f0-9]{32}', backup_id) is None:
            raise PersistenceError('invalid_backup_id', 'backup')
        with self.records.transaction(write=True) as c:
            row = c.execute('SELECT * FROM restore_jobs WHERE id=?', (request_id,)).fetchone()
            if row:
                if row['backup_id'] != backup_id:
                    raise PersistenceError('record_conflict', 'backup')
            else:
                if c.execute("SELECT COUNT(*) FROM restore_jobs WHERE state IN ('pending','running')").fetchone()[0] >= 3:
                    raise PersistenceError('backup_queue_full', 'backup')
                c.execute("INSERT INTO restore_jobs VALUES (?,?,'pending',NULL,'queued',?,NULL)", (request_id, backup_id, to_rfc3339(self.records.clock())))
                row = c.execute('SELECT * FROM restore_jobs WHERE id=?', (request_id,)).fetchone()
        return dict(row)

    def claim_restore(self):
        with self.records.transaction(write=True) as c:
            row = c.execute("SELECT * FROM restore_jobs WHERE state='pending' ORDER BY created_at,id LIMIT 1").fetchone()
            if row:
                c.execute("UPDATE restore_jobs SET state='running',result_code='verifying' WHERE id=?", (row['id'],))
        return dict(row) if row else None

    def finish_restore(self, request_id, *, candidate_name=None, code='candidate_verified'):
        with self.records.transaction(write=True) as c:
            c.execute("UPDATE restore_jobs SET state=?,candidate_name=?,result_code=?,finished_at=? WHERE id=? AND state='running'", ('verified' if candidate_name else 'failed', candidate_name, code, to_rfc3339(self.records.clock()), request_id))

    def completed_streams(self, covered):
        # Query only recorded streams. Old imported history never triggers new jobs.
        with self.records.transaction() as c:
            rows = c.execute("SELECT id FROM streams WHERE channel_id=? AND ended_at IS NOT NULL AND source='bot' ORDER BY ended_at DESC,id",
                             (self.records.channel_id,))
            result = []
            for row in rows:
                if row['id'] not in covered:
                    result.append(row['id'])
                    if len(result) == 1000:
                        break
        return result
