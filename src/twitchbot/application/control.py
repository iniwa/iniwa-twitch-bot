"""Local control definitions and an explicit outbound-action contract."""

from dataclasses import dataclass
from typing import Protocol

from .analytics import identifier
from .community import text_field
from .persistence import PersistenceError


@dataclass(frozen=True, slots=True)
class ChannelPreset:
    id: str
    name: str
    title: str
    game_id: str | None
    game_name: str | None
    tags: tuple[str, ...] = ()
    social_tags: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.id)
        text_field(self.name, 100)
        text_field(self.title, 140)
        if not self.name.strip() or not self.title.strip():
            raise PersistenceError("empty_preset", "control")
        if self.game_id is not None:
            identifier(self.game_id)
        text_field(self.game_name, 200, optional=True)
        for tags in (self.tags, self.social_tags):
            if not isinstance(tags, tuple) or len(tags)>10 or any(not isinstance(tag,str) or not tag.strip() or len(tag)>100 for tag in tags) or len(set(tags)) != len(tags):
                raise PersistenceError("invalid_tags", "control")
        if any(len(tag)>25 or not tag.isalnum() for tag in self.tags):
            raise PersistenceError('invalid_tags', 'control')


@dataclass(frozen=True, slots=True)
class ActionResult:
    state: str
    remote_id: str | None = None
    position_seconds: int | None = None

    def __post_init__(self):
        if self.state not in ("succeeded", "partial", "failed", "unknown"):
            raise PersistenceError("invalid_action_result", "control")
        if self.remote_id is not None:
            identifier(self.remote_id)
        if self.position_seconds is not None and (type(self.position_seconds) is not int or self.position_seconds < 0):
            raise PersistenceError("invalid_action_result", "control")


@dataclass(frozen=True, slots=True)
class ChannelUpdate:
    """Only fields intended for Twitch; local names/social tags are excluded."""
    title: str
    game_id: str | None
    tags: tuple[str, ...]


class TwitchControl(Protocol):
    """An adapter must implement pacing/auth and confirm success before returning it.

    A timeout/transport exception after dispatch is always an unknown result.
    Construction and availability inspection must not perform network I/O.
    """
    available: bool

    def create_marker(self, channel_id: str, description: str) -> ActionResult: ...

    def apply_preset(self, channel_id: str, preset: ChannelUpdate) -> ActionResult: ...


class UnavailableTwitchControl:
    available = False

    def create_marker(self, channel_id, description):
        raise PersistenceError("control_unavailable", "control")

    def apply_preset(self, channel_id, preset):
        raise PersistenceError("control_unavailable", "control")
