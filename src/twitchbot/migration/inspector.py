"""Read-only, bounded, and privacy-safe legacy source inspection."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import stat
import time
from typing import Any

from ..settings import AppSettings, SettingsValidationError

_REFERENCE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_STREAM = re.compile(r"^history/stream_[A-Za-z0-9_-]{1,128}\.jsonl$")
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CREDENTIAL_WORDS = ("token", "secret", "password", "authorization", "credential")
_MAX_JSON = 64 * 1024 * 1024
_MAX_LINE = 2 * 1024 * 1024

_CONFIG = frozenset({
    "client_id", "broadcaster_id", "bot_user_id", "channel_name", "is_running",
    "enable_welcome", "ignore_stream_status", "enable_vod_download", "hide_self_bot",
    "ignored_users", "rules", "presets", "prediction_presets", "layout", "debug_mode",
    "current_title", "current_tweet_tags", "access_token", "broadcaster_token",
})
_VIEWER = frozenset({
    "name", "login", "total_visits", "streak", "total_duration", "last_stream_id",
    "last_seen_ts", "total_comments", "total_bits", "is_sub", "total_sub_months",
    "last_sub_ts", "last_sub_plan", "total_gifts_given", "total_gifts_received",
    "followed_at", "unfollowed_at", "memo", "is_follower",
})
_INDEX = frozenset({
    "start_time", "title", "game_name", "max_viewers", "avg_viewers_sum", "log_count",
    "avg_viewers", "follower_count", "duration", "thumbnail_url", "source", "vod_status",
    "vod_id", "file_path",
})
_TRANSIENT = frozenset({"sid", "encode_status", "archive_file_size", "duration_short"})
_JSONL = frozenset({
    "timestamp", "stream_info", "metrics", "emotes", "subs", "raids", "points",
    "badges", "messages", "events", "census",
})
_NUMERIC = (int, float)


class InspectionError(RuntimeError):
    """A typed failure whose message intentionally contains no caller data."""

    def __init__(self, code: str, context: str = "source") -> None:
        self.code = code
        self.context = context
        super().__init__(f"inspection error ({code}) in {context}")


def _safe_field(value: object) -> str | None:
    return value if type(value) is str and _FIELD.fullmatch(value) else None


def _number(value: object) -> bool:
    return type(value) in _NUMERIC


def _utc(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise InspectionError("invalid_clock", "clock")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("non_standard_constant")


def _json(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise InspectionError("empty_document", "json")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise InspectionError("bom", "json")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except UnicodeDecodeError as exc:
        raise InspectionError("invalid_utf8", "json") from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InspectionError("malformed_json", "json") from exc
    if not isinstance(value, dict):
        raise InspectionError("document_not_object", "json")
    return value


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    name: str
    size: int
    checksum: str
    mtime_ns: int

    def safe(self) -> dict[str, object]:
        return {"name": self.name, "size": self.size, "checksum": self.checksum}

    def observation(self) -> dict[str, object]:
        return {"name": self.name, "mtime_ns": self.mtime_ns}


@dataclass(frozen=True, slots=True)
class UnsupportedEntry:
    name: str
    kind: str
    size: int
    mtime_ns: int

    def safe(self) -> dict[str, object]:
        return {"name": self.name, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class InspectionReport:
    source_reference: str
    cutoff: str
    elapsed_ms: int
    manifest: tuple[ManifestEntry, ...]
    unsupported: tuple[UnsupportedEntry, ...]
    documents: tuple[tuple[str, str, int, int, int], ...]
    issues: tuple[tuple[str, int | None, str, str, str | None], ...]
    unknown_fields: tuple[tuple[str, str | None, int], ...]
    credentials: tuple[tuple[str, str | None, str], ...]
    vod_counts: tuple[tuple[str, int], ...]
    source_unchanged: bool = True
    credentials_redacted: bool = True

    def to_safe_mapping(self) -> dict[str, object]:
        return {
            "importer_version": 1,
            "report_version": 1,
            "source_reference": self.source_reference,
            "cutoff": self.cutoff,
            "elapsed_ms": self.elapsed_ms,
            "manifest": [entry.safe() for entry in self.manifest],
            "observations": [entry.observation() for entry in self.manifest],
            "documents": [
                {"file": name, "status": status, "records_read": read, "valid": valid, "rejected": rejected}
                for name, status, read, valid, rejected in self.documents
            ],
            "unsupported": [entry.safe() for entry in self.unsupported],
            "issues": [
                {"file": file, "line": line, "entity": entity, "code": code, "field": field}
                for file, line, entity, code, field in self.issues
            ],
            "unknown_fields": [
                {"entity": entity, "field": field, "count": count}
                for entity, field, count in self.unknown_fields
            ],
            "credentials": [
                {"role": role, "key": key, "state": state}
                for role, key, state in self.credentials
            ],
            "vod_path_counts": dict(self.vod_counts),
            "credentials_redacted": True,
            "source_unchanged": True,
        }


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    source_reference: str
    blockers: tuple[str, ...]
    import_ready: bool = False

    def to_safe_mapping(self) -> dict[str, object]:
        return {"source_reference": self.source_reference, "import_ready": False, "blockers": list(self.blockers)}


class LegacySourceInspector:
    """Inspect only an allowlisted staged source when ``inspect`` is called."""

    def __init__(
        self,
        source_root: str | Path,
        downloads_root: str | Path,
        source_reference: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source_root = self._path(source_root, "source_root")
        self._downloads_root = self._path(downloads_root, "downloads_root")
        if not isinstance(source_reference, str) or _REFERENCE.fullmatch(source_reference) is None:
            raise InspectionError("invalid_source_reference", "source_reference")
        if not callable(clock) or not callable(monotonic):
            raise InspectionError("invalid_callable", "inspector")
        self._reference = source_reference
        self._clock = clock
        self._monotonic = monotonic

    @staticmethod
    def _path(value: object, context: str) -> Path:
        if not isinstance(value, (str, Path)):
            raise InspectionError("invalid_source_path", context)
        try:
            path = Path(value)
        except (TypeError, ValueError) as exc:
            raise InspectionError("invalid_source_path", context) from exc
        if not path.is_absolute():
            raise InspectionError("invalid_source_path", context)
        return path

    @staticmethod
    def _safe_lstat(path: Path, context: str):
        try:
            return path.lstat()
        except OSError as exc:
            raise InspectionError("source_stat_failed", context) from exc

    @staticmethod
    def _link_or_special(path: Path, context: str) -> None:
        mode = LegacySourceInspector._safe_lstat(path, context).st_mode
        try:
            junction = getattr(path, "is_junction", lambda: False)()
        except OSError as exc:
            raise InspectionError("source_stat_failed", context) from exc
        if stat.S_ISLNK(mode) or junction or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise InspectionError("unsafe_source_entry", context)

    def _validate_roots(self) -> None:
        for path, context in ((self._source_root, "source"), (self._downloads_root, "downloads")):
            self._link_or_special(path, context)
            try:
                is_directory = path.is_dir()
            except OSError as exc:
                raise InspectionError("source_stat_failed", context) from exc
            if not is_directory:
                raise InspectionError("invalid_source_root", context)

    def _inventory(self) -> tuple[tuple[ManifestEntry, ...], tuple[UnsupportedEntry, ...]]:
        self._validate_roots()
        root = self._source_root
        try:
            candidates = list(root.iterdir())
        except OSError as exc:
            raise InspectionError("source_read_failed", "source") from exc
        history = root / "history"
        try:
            history_exists = history.exists()
        except OSError as exc:
            raise InspectionError("source_stat_failed", "history") from exc
        if history_exists:
            self._link_or_special(history, "history")
            if not history.is_dir():
                raise InspectionError("unsafe_source_entry", "history")
            try:
                candidates.extend(history.iterdir())
            except OSError as exc:
                raise InspectionError("source_read_failed", "history") from exc

        allowed = {"config.json", "viewers.json", "history/stream_index.json"}
        entries: list[ManifestEntry] = []
        unsupported: list[UnsupportedEntry] = []
        seen: set[str] = set()
        for path in candidates:
            try:
                name = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise InspectionError("unsafe_source_entry", "source") from exc
            if name in seen:
                continue
            seen.add(name)
            self._link_or_special(path, "source")
            if name == "history" and path.is_dir():
                continue
            accepted = name in allowed or _STREAM.fullmatch(name) is not None
            if not accepted:
                source_stat = self._safe_lstat(path, "source")
                kind = "directory" if stat.S_ISDIR(source_stat.st_mode) else "file"
                unsupported.append(UnsupportedEntry(name, kind, source_stat.st_size, source_stat.st_mtime_ns))
                continue
            if not path.is_file():
                raise InspectionError("unsafe_source_entry", "source")
            try:
                source_stat = path.stat()
                digest = sha256()
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as exc:
                raise InspectionError("source_read_failed", "source") from exc
            entries.append(ManifestEntry(name, source_stat.st_size, digest.hexdigest(), source_stat.st_mtime_ns))
        return tuple(sorted(entries, key=lambda entry: entry.name)), tuple(sorted(unsupported, key=lambda entry: entry.name))

    @staticmethod
    def _issue(
        issues: list[tuple[str, int | None, str, str, str | None]],
        file: str,
        line: int | None,
        entity: str,
        code: str,
        field: object = None,
    ) -> None:
        issues.append((file, line, entity, code, _safe_field(field)))

    def inspect(self) -> InspectionReport:
        try:
            started = self._monotonic()
            if type(started) not in (int, float):
                raise TypeError
        except Exception as exc:
            raise InspectionError("invalid_monotonic", "clock") from exc
        try:
            cutoff = _utc(self._clock())
        except InspectionError:
            raise
        except Exception as exc:
            raise InspectionError("invalid_clock", "clock") from exc

        before, unsupported = self._inventory()
        indexed = {entry.name: entry for entry in before}
        documents: list[tuple[str, str, int, int, int]] = []
        issues: list[tuple[str, int | None, str, str, str | None]] = []
        unknown: Counter[tuple[str, str | None]] = Counter()
        credentials: list[tuple[str, str | None, str]] = []
        vod: Counter[str] = Counter()

        for entry in unsupported:
            self._issue(issues, entry.name, None, "source", "unsupported_entry")
        for required in ("config.json", "viewers.json", "history/stream_index.json"):
            if required not in indexed:
                documents.append((required, "missing", 0, 0, 0))
                self._issue(issues, required, None, "document", "missing_document")

        for entry in before:
            path = self._source_root.joinpath(*entry.name.split("/"))
            if _STREAM.fullmatch(entry.name) is not None:
                documents.append(self._jsonl(path, entry.name, issues, unknown))
                continue
            if entry.size > _MAX_JSON:
                documents.append((entry.name, "too_large", 0, 0, 0))
                self._issue(issues, entry.name, None, "document", "document_too_large")
                continue
            try:
                with path.open("rb") as handle:
                    document = _json(handle.read())
            except InspectionError as exc:
                documents.append((entry.name, "invalid", 0, 0, 1))
                self._issue(issues, entry.name, None, "document", exc.code)
                continue
            except OSError as exc:
                raise InspectionError("source_read_failed", "document") from exc

            if entry.name == "config.json":
                self._config(document, issues, unknown, credentials)
                documents.append((entry.name, "valid", 1, 1, 0))
            elif entry.name == "viewers.json":
                read, valid, rejected = self._entities(document, "viewer", entry.name, issues, unknown, vod)
                documents.append((entry.name, "valid" if not rejected else "partial", read, valid, rejected))
            elif entry.name == "history/stream_index.json":
                read, valid, rejected = self._entities(document, "stream_index", entry.name, issues, unknown, vod)
                documents.append((entry.name, "valid" if not rejected else "partial", read, valid, rejected))

        after, unsupported_after = self._inventory()
        if after != before or unsupported_after != unsupported:
            raise InspectionError("source_changed", "source")
        try:
            ended = self._monotonic()
            if type(ended) not in (int, float):
                raise TypeError
            elapsed = max(0, int((ended - started) * 1000))
        except Exception as exc:
            raise InspectionError("invalid_monotonic", "clock") from exc
        return InspectionReport(
            self._reference, cutoff, elapsed, before, unsupported, tuple(sorted(documents)), tuple(issues),
            tuple((entity, field, count) for (entity, field), count in sorted(unknown.items(), key=lambda item: (item[0][0], item[0][1] or ""))),
            tuple(sorted(credentials)), tuple(sorted(vod.items())),
        )

    def _config(self, document: dict[str, Any], issues: list, unknown: Counter, credentials: list) -> None:
        for key, value in document.items():
            safe = _safe_field(key)
            if safe is None:
                self._issue(issues, "config.json", None, "config", "unsafe_field_name")
                continue
            if any(word in key.casefold() for word in _CREDENTIAL_WORDS):
                role = "bot" if key == "access_token" else "broadcaster" if key == "broadcaster_token" else "unknown"
                if type(value) is not str:
                    self._issue(issues, "config.json", None, "credential", "invalid_credential_shape", key)
                credentials.append((role, key, "configured" if type(value) is str and bool(value) else "not_configured"))
                continue
            if key not in _CONFIG:
                unknown[("config", key)] += 1
            elif key in {"client_id", "broadcaster_id", "bot_user_id", "channel_name", "current_title", "current_tweet_tags"} and value is not None and type(value) is not str:
                self._issue(issues, "config.json", None, "config", "invalid_field", key)
            elif key in {"rules", "presets", "prediction_presets", "layout"} and value is not None and not isinstance(value, (dict, list)):
                self._issue(issues, "config.json", None, "config", "invalid_field", key)
        mapped = {
            "bot_enabled": document.get("is_running", False),
            "welcome_enabled": document.get("enable_welcome", False),
            "ignore_stream_status": document.get("ignore_stream_status", False),
            "enable_vod_download": document.get("enable_vod_download", False),
            "hide_self_bot": document.get("hide_self_bot", False),
            "ignored_users": document.get("ignored_users", ()),
        }
        try:
            AppSettings.from_mapping(mapped)
        except SettingsValidationError as exc:
            self._issue(issues, "config.json", None, "config", exc.code, exc.field)

    def _entities(self, document: dict[str, Any], entity: str, file: str, issues: list, unknown: Counter, vod: Counter) -> tuple[int, int, int]:
        allowed = _VIEWER if entity == "viewer" else _INDEX | _TRANSIENT
        numeric = {
            "total_visits", "streak", "total_duration", "total_comments", "total_bits", "total_sub_months",
            "total_gifts_given", "total_gifts_received", "max_viewers", "avg_viewers_sum", "log_count",
            "avg_viewers", "follower_count", "archive_file_size",
        }
        strings = {
            "name", "login", "last_stream_id", "last_seen_ts", "last_sub_ts", "last_sub_plan", "followed_at",
            "unfollowed_at", "memo", "start_time", "title", "game_name", "duration", "thumbnail_url", "source",
            "vod_status", "vod_id", "sid", "encode_status", "duration_short",
        }
        read = valid = rejected = 0
        for record in document.values():
            read += 1
            if not isinstance(record, dict):
                self._issue(issues, file, None, entity, "invalid_record")
                rejected += 1
                continue
            invalid = False
            for key, value in record.items():
                safe = _safe_field(key)
                if safe is None:
                    self._issue(issues, file, None, entity, "unsafe_field_name")
                    invalid = True
                    continue
                if key not in allowed:
                    unknown[(entity, key)] += 1
                    continue
                if key in numeric and not _number(value):
                    self._issue(issues, file, None, entity, "invalid_field", key)
                    invalid = True
                elif key in ('last_seen_ts','last_sub_ts') and (_number(value) or value is None):
                    pass  # Legacy workers store Unix seconds; importer validates range.
                elif key in ("is_sub", "is_follower") and type(value) is not bool:
                    self._issue(issues, file, None, entity, "invalid_field", key)
                    invalid = True
                elif key in strings and value is not None and type(value) is not str:
                    self._issue(issues, file, None, entity, "invalid_field", key)
                    invalid = True
                elif key == "file_path":
                    vod[self._vod(value)] += 1
            if entity == "stream_index" and "file_path" not in record:
                vod["absent"] += 1
            if invalid:
                rejected += 1
            else:
                valid += 1
        return read, valid, rejected

    @staticmethod
    def _path_is_linklike(path: Path) -> bool:
        try:
            return path.is_symlink() or getattr(path, "is_junction", lambda: False)()
        except OSError as exc:
            raise InspectionError("source_stat_failed", "vod") from exc

    def _vod_components_safe(self, candidate: Path) -> bool:
        """Reject every existing component that could redirect the media path."""
        try:
            base = self._downloads_root
            parts = candidate.relative_to(base).parts
        except ValueError:
            return False
        current = base
        if self._path_is_linklike(current):
            return False
        for part in parts:
            current = current / part
            # Check the directory entry before exists(): a broken symlink has
            # no target for exists(), but is still an unsafe redirection.
            if self._path_is_linklike(current):
                return False
            try:
                exists = current.exists()
            except OSError as exc:
                raise InspectionError("source_stat_failed", "vod") from exc
        return True

    def _classify_candidate(self, candidate: Path, absolute: bool, outside_code: str = "absolute_outside") -> str:
        try:
            candidate.relative_to(self._downloads_root)
        except ValueError:
            lexically_inside = False
        else:
            lexically_inside = True
        if lexically_inside and not self._vod_components_safe(candidate):
            return "symlink_escape"
        try:
            base = self._downloads_root.resolve(strict=False)
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(base)
        except (OSError, ValueError):
            return outside_code if absolute else "symlink_escape"
        if not self._vod_components_safe(candidate):
            return "symlink_escape"
        try:
            exists = candidate.exists()
        except OSError as exc:
            raise InspectionError("source_stat_failed", "vod") from exc
        if not exists:
            return "absolute_inside_missing_target" if absolute else "missing_target"
        try:
            is_file = candidate.is_file()
        except OSError as exc:
            raise InspectionError("source_stat_failed", "vod") from exc
        if not is_file:
            return "absolute_inside_non_file" if absolute else "non_file_target"
        return "absolute_inside" if absolute else "safe_relative"

    def _vod(self, value: object) -> str:
        if value is None:
            return "absent"
        if type(value) is not str:
            return "invalid_path"
        if not value:
            return "empty"
        # Native absolute paths are analysed before foreign Windows spelling.
        native = Path(value)
        if native.is_absolute():
            return self._classify_candidate(
                native,
                True,
                "posix_outside" if value.startswith("/") else "absolute_outside",
            )
        if value.startswith("/"):
            return "posix_outside"
        if value.startswith("\\\\") or re.match(r"^[A-Za-z]:[\\/]", value):
            return "windows_outside"
        parts = value.replace("\\", "/").split("/")
        if any(part in ("", ".", "..") for part in parts):
            return "traversal"
        return self._classify_candidate(self._downloads_root.joinpath(*parts), False)

    @staticmethod
    def _unknown(unknown: Counter, entity: str, prefix: str, key: object) -> None:
        safe = _safe_field(key)
        if safe is not None:
            unknown[(entity, f"{prefix}.{safe}")] += 1

    def _object_fields(self, value: object, known: dict[str, type | tuple[type, ...]], entity: str, prefix: str, unknown: Counter, issues: list, file: str, line: int) -> bool:
        if not isinstance(value, dict):
            return False
        valid = True
        for key, child in value.items():
            safe = _safe_field(key)
            if safe is None:
                self._issue(issues, file, line, entity, "unsafe_field_name")
                valid = False
            elif key not in known:
                self._unknown(unknown, entity, prefix, key)
            elif known[key] is _NUMERIC:
                if not _number(child):
                    self._issue(issues, file, line, entity, "invalid_field", key)
                    valid = False
            elif not isinstance(child, known[key]):
                self._issue(issues, file, line, entity, "invalid_field", key)
                valid = False
        return valid

    def _list_items(self, value: object, required: dict[str, object], known: set[str], entity: str, prefix: str, unknown: Counter, issues: list, file: str, line: int, optional: dict[str, object] | None = None) -> bool:
        if not isinstance(value, list):
            return False
        valid = True
        for item in value:
            if not isinstance(item, dict):
                valid = False
                continue
            for field, expected in required.items():
                if field not in item:
                    valid = False
                    continue
                value_at_field = item[field]
                if expected is _NUMERIC:
                    is_valid = _number(value_at_field)
                else:
                    is_valid = isinstance(value_at_field, expected)
                if not is_valid:
                    self._issue(issues, file, line, entity, "invalid_field", field)
                    valid = False
            for field, expected in (optional or {}).items():
                if field not in item:
                    continue
                value_at_field = item[field]
                if expected is _NUMERIC:
                    is_valid = _number(value_at_field)
                else:
                    is_valid = isinstance(value_at_field, expected)
                if not is_valid:
                    self._issue(issues, file, line, entity, "invalid_field", field)
                    valid = False
            for key in item:
                safe = _safe_field(key)
                if safe is None:
                    self._issue(issues, file, line, entity, "unsafe_field_name")
                    valid = False
                elif key not in known:
                    self._unknown(unknown, entity, prefix, key)
        return valid

    def _jsonl_shape(self, item: dict[str, Any], name: str, line: int, issues: list, unknown: Counter) -> bool:
        if type(item.get("timestamp")) is not str or not isinstance(item.get("stream_info"), dict) or not isinstance(item.get("metrics"), dict):
            return False
        valid = True
        for key, value in item.items():
            safe = _safe_field(key)
            if safe is None:
                self._issue(issues, name, line, "jsonl", "unsafe_field_name")
                valid = False
                continue
            if key not in _JSONL:
                unknown[("jsonl", key)] += 1
                continue
            if key == "stream_info":
                stream_info_valid = self._object_fields(value, {"title": str, "game": str, "tags": list, "follower_total": _NUMERIC}, "jsonl", "stream_info", unknown, issues, name, line)
                if isinstance(value, dict) and "tags" in value and (
                    not isinstance(value["tags"], list) or any(type(tag) is not str for tag in value["tags"])
                ):
                    self._issue(issues, name, line, "jsonl", "invalid_field", "tags")
                    stream_info_valid = False
                valid = stream_info_valid and valid
            elif key == "metrics":
                valid = self._object_fields(value, {"viewer_count": _NUMERIC, "chat_count": _NUMERIC, "msg_speed": _NUMERIC, "bits": _NUMERIC, "gift_subs": _NUMERIC}, "jsonl", "metrics", unknown, issues, name, line) and valid
            elif key in {"emotes", "badges"}:
                if not isinstance(value, dict):
                    valid = False
            elif key == "subs":
                if not isinstance(value, dict):
                    valid = False
                else:
                    for plan, count in value.items():
                        if _safe_field(plan) is None:
                            self._issue(issues, name, line, "jsonl", "unsafe_field_name")
                            valid = False
                        elif plan not in {"Prime", "Tier1", "Tier2", "Tier3"}:
                            self._unknown(unknown, "jsonl", "subs", plan)
                        elif not _number(count):
                            self._issue(issues, name, line, "jsonl", "invalid_field", plan)
                            valid = False
            elif key == "raids":
                valid = self._list_items(value, {"user": str, "count": _NUMERIC}, {"user", "count"}, "jsonl", "raids", unknown, issues, name, line) and valid
            elif key == "points":
                valid = self._list_items(value, {"user": str, "reward_id": str, "text": str}, {"user", "reward_id", "text"}, "jsonl", "points", unknown, issues, name, line) and valid
            elif key == "messages":
                valid = self._list_items(value, {"time": str, "user": str, "text": str, "is_sub": bool, "badges": str}, {"time", "user", "text", "is_sub", "badges"}, "jsonl", "messages", unknown, issues, name, line) and valid
            elif key == "events":
                event_optional = {
                    "user": str, "user_id": str, "amount": _NUMERIC, "plan": str,
                    "months": _NUMERIC, "msg_id": str, "recipient": str,
                    "recipient_id": str, "count": _NUMERIC,
                }
                valid = self._list_items(value, {"type": str}, {"type", *event_optional}, "jsonl", "events", unknown, issues, name, line, event_optional) and valid
            elif key == "census":
                valid = self._list_items(value, {"id": str, "name": str, "is_sub": bool, "is_follower": bool}, {"id", "name", "is_sub", "is_follower"}, "jsonl", "census", unknown, issues, name, line) and valid
        return valid

    def _jsonl(self, path: Path, name: str, issues: list, unknown: Counter) -> tuple[str, str, int, int, int]:
        read = valid = rejected = 0
        try:
            with path.open("rb") as handle:
                line = 0
                while True:
                    raw = handle.readline(_MAX_LINE + 1)
                    if not raw:
                        break
                    line += 1
                    read += 1
                    if len(raw) > _MAX_LINE:
                        while raw and not raw.endswith(b"\n"):
                            raw = handle.readline(_MAX_LINE + 1)
                        rejected += 1
                        self._issue(issues, name, line, "jsonl", "line_too_large")
                        continue
                    if not raw.strip():
                        rejected += 1
                        self._issue(issues, name, line, "jsonl", "blank_line")
                        continue
                    try:
                        item = _json(raw)
                    except InspectionError as exc:
                        rejected += 1
                        self._issue(issues, name, line, "jsonl", exc.code)
                        continue
                    if not self._jsonl_shape(item, name, line, issues, unknown):
                        rejected += 1
                        self._issue(issues, name, line, "jsonl", "invalid_shape")
                        continue
                    valid += 1
        except OSError as exc:
            raise InspectionError("source_read_failed", "jsonl") from exc
        if read == 0:
            self._issue(issues, name, None, "jsonl", "empty_document")
            return name, "empty", 0, 0, 0
        return name, "valid" if not rejected else "partial", read, valid, rejected

    def verify_unchanged(self, report: InspectionReport) -> None:
        if not isinstance(report, InspectionReport) or report.source_reference != self._reference:
            raise InspectionError("invalid_report", "verify")
        current, unsupported = self._inventory()
        if current != report.manifest or unsupported != report.unsupported:
            raise InspectionError("source_changed", "verify")


def build_migration_plan(report: InspectionReport) -> MigrationPlan:
    if not isinstance(report, InspectionReport):
        raise InspectionError("invalid_report", "plan")
    blockers = {"domain_schema_unavailable"}
    if report.issues or any(status == "missing" for _, status, *_ in report.documents):
        blockers.add("invalid_source")
    if any(state == "configured" for _, _, state in report.credentials):
        blockers.add("credential_validation_required")
    return MigrationPlan(report.source_reference, tuple(sorted(blockers)))
