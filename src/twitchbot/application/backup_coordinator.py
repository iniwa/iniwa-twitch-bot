"""One explicit scheduler step; no threads, timers or startup side effects."""

from datetime import timedelta

from .backups import daily_backup_due
from .analytics import identifier
from .persistence import PersistenceError


class BackupCoordinator:
    """A future runtime worker supplies durable completed-stream IDs and calls step.

    Successful manifests deduplicate days/streams across restarts. Failed requests
    remain due; a bounded retry delay avoids repeatedly touching a failed NAS.
    """
    def __init__(self, service, *, running=None):
        self.service=service
        self.running=running if running is not None else lambda:False
        self.retry_after=None

    def step(self, completed_stream_ids=(), *, daily_hour=4):
        if not self.running():
            return {"state":"paused"}
        now=self.service.clock()
        if self.retry_after is not None and now<self.retry_after:
            return {"state":"waiting"}
        transfer_error=None
        try:
            if not isinstance(completed_stream_ids,(list,tuple)) or len(completed_stream_ids)>1000:
                raise PersistenceError("invalid_backup_streams", "backup")
            for stream_id in completed_stream_ids:
                identifier(stream_id)
            items=self.service.list_backups()
            # Transfer oldest first; a failed NAS must not prevent an otherwise
            # affordable current local copy from being made.
            pending=[item for item in reversed(items) if item["state"] in ("local_ready","transfer_failed")]
            if pending and self.service.transfer is not None:
                try:
                    self.service.publish(pending[0]["id"])
                except PersistenceError as exc:
                    transfer_error=exc.code
            if not self.running():
                return {"state":"paused"}
            days=[item["daily_day"] for item in items if item.get("daily_day")]
            day=daily_backup_due(now,max(days) if days else None,running=True,hour=daily_hour)
            covered={sid for item in items for sid in item.get("stream_ids",[])}
            streams=tuple(sorted(set(completed_stream_ids)-covered))
            reasons=tuple((['daily'] if day else [])+(['stream_end'] if streams else []))
            result={"state":"up_to_date"}
            if reasons:
                item=self.service.create(reasons=reasons,stream_ids=streams,cancelled=lambda:not self.running(),daily_hour=daily_hour)
                result={"state":"local_ready","backup_id":item["id"]}
            if transfer_error:
                self.retry_after=now+timedelta(minutes=5)
                result["transfer_error"]=transfer_error
            return result
        except PersistenceError as exc:
            self.retry_after=now+timedelta(minutes=5)
            return {"state":"deferred","reason":exc.code}
