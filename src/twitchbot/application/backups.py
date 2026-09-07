"""Backup scheduling decisions and a host-owned, verified transfer boundary."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Mapping

from .analytics import timestamp

JST = timezone(timedelta(hours=9))


def daily_backup_due(now: datetime, last_day: str | None, *, running: bool, hour: int = 4):
    timestamp(now)
    if type(hour) is not int or not 0 <= hour <= 23:
        raise ValueError('invalid daily hour')
    if not running:
        return None
    local = now.astimezone(JST)
    day = (local.date() - timedelta(days=local.hour < hour)).isoformat()
    return day if last_day is None or last_day < day else None


@dataclass(frozen=True, slots=True)
class TransferReceipt:
    backup_id: str
    checksum: str
    size_bytes: int
    destination_verified: bool


class BackupTransfer(Protocol):
    """Host integration must verify the NAS identity, not just directory existence.

    Publish the same ID idempotently through an incomplete name, check contents,
    then promote atomically. Return a receipt only after destination verification.
    Credentials, mounts, remote paths and retries belong to that integration.
    """
    def publish(self, source: Path, backup_id: str, checksum: str, metadata: Mapping) -> TransferReceipt: ...


def retention_candidates(manifests):
    """Plan only: 14 daily days, four weeks, three stream ends; no file deletion."""
    successful=sorted((item for item in manifests if item["state"]=="nas_verified"),key=lambda item:item["created_at"],reverse=True)
    if not successful:
        return ()
    keep={successful[0]["id"]}
    days,weeks=set(),set()
    stream_count=0
    for item in successful:
        local=datetime.fromisoformat(item["created_at"].replace("Z","+00:00")).astimezone(JST)
        day=item.get('daily_day') or (local.date()-timedelta(days=local.hour<4)).isoformat()
        week=local.isocalendar()[:2]
        if "manual" in item["reasons"] or "before_change" in item["reasons"]:
            keep.add(item["id"])
        if "daily" in item["reasons"] and day not in days and len(days)<14:
            days.add(day);keep.add(item["id"])
        if week not in weeks and len(weeks)<4:
            weeks.add(week);keep.add(item["id"])
        if "stream_end" in item["reasons"] and stream_count<3:
            stream_count+=1;keep.add(item["id"])
    return tuple(item["id"] for item in successful if item["id"] not in keep)
