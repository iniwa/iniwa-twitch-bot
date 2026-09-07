"""Normalized recording inputs. No Twitch payloads or chat copies in event logs."""

from dataclasses import dataclass
from datetime import datetime

from .analytics import identifier, timestamp
from .persistence import PersistenceError


def text_field(value, limit, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise PersistenceError("invalid_text", "community")


@dataclass(frozen=True, slots=True)
class Person:
    user_id: str
    login: str | None = None
    display_name: str | None = None

    def __post_init__(self):
        identifier(self.user_id)
        text_field(self.login, 100, optional=True)
        text_field(self.display_name, 200, optional=True)


@dataclass(frozen=True, slots=True)
class ChannelEvent:
    id: str
    kind: str
    occurred_at: datetime | None
    received_at: datetime
    person: Person | None = None
    stream_id: str | None = None
    attribution: str = "unknown"
    amount: int | None = None

    def __post_init__(self):
        identifier(self.id)
        if self.kind not in ("follow", "subscribe", "resubscribe", "gift_subscription", "cheer", "raid", "redemption", "prediction"):
            raise PersistenceError("invalid_event_kind", "community")
        timestamp(self.received_at)
        if self.occurred_at is not None and timestamp(self.occurred_at) > self.received_at:
            raise PersistenceError("invalid_event_time", "community")
        if self.person is not None and not isinstance(self.person, Person):
            raise PersistenceError("invalid_person", "community")
        if self.kind == "follow" and (self.person is None or self.occurred_at is None):
            raise PersistenceError("invalid_follow", "community")
        if self.attribution not in ("stream", "offline", "unknown"):
            raise PersistenceError("invalid_attribution", "community")
        if (self.stream_id is not None) != (self.attribution == "stream"):
            raise PersistenceError("invalid_attribution", "community")
        if self.attribution != "unknown" and self.occurred_at is None:
            raise PersistenceError("attribution_time_unknown", "community")
        if self.stream_id is not None:
            identifier(self.stream_id)
        if self.amount is not None and (type(self.amount) is not int or not 0 <= self.amount <= 2**63-1):
            raise PersistenceError("invalid_amount", "community")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    id: str
    person: Person
    stream_id: str | None
    occurred_at: datetime
    received_at: datetime
    body: str

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.person, Person):
            raise PersistenceError("invalid_person", "community")
        if self.stream_id is not None:
            identifier(self.stream_id)
        timestamp(self.occurred_at)
        if timestamp(self.received_at) < self.occurred_at:
            raise PersistenceError("invalid_event_time", "community")
        text_field(self.body, 5000)


@dataclass(frozen=True, slots=True)
class Follower:
    person: Person
    followed_at: datetime

    def __post_init__(self):
        if not isinstance(self.person, Person):
            raise PersistenceError("invalid_person", "community")
        timestamp(self.followed_at)
