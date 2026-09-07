from datetime import datetime, timezone
from pathlib import Path

import pytest

from twitchbot.adapters.persistence import SQLiteDatabase
from twitchbot.adapters.persistence.migrations import MIGRATIONS, Migration
from twitchbot.adapters.persistence.repositories import (
    ChannelReadModelRepository,
    ImportBatchRepository,
    OperationLogRepository,
    ProcessedEventRepository,
    SettingsRepository,
)
from twitchbot.application.persistence import ChannelReadModel, ImportBatch, OperationRecord, RevisionConflictError, thaw_json
from twitchbot.adapters.persistence.sqlite import DEFAULT_DATABASE_PATH
from twitchbot.application.persistence import PersistenceError
from twitchbot.settings import AppSettings


def test_database_is_inert_and_enforces_explicit_path_policy(tmp_path):
    assert DEFAULT_DATABASE_PATH == "/app/data/twitchbot-v2.sqlite3"
    path = tmp_path / "core.sqlite3"
    SQLiteDatabase(path)
    assert not path.exists()
    for invalid in ("relative.sqlite3", tmp_path / "data.db", tmp_path / "core.db"):
        with pytest.raises(PersistenceError):
            SQLiteDatabase(invalid)


def test_core_migration_0001_has_fixed_compatibility_identity():
    """This golden value protects already-created v2 core databases."""
    core = MIGRATIONS[0]
    assert core.version == 1
    assert core.name == "core_system"
    assert core.checksum == "fcf799410ccb568b4cb07b49f161c0e3d41fe135197c5a8078508d8a0c663101"


def test_migration_connection_and_integrity_contract(tmp_path):
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    database = SQLiteDatabase(tmp_path / "core.sqlite3", clock=lambda: now)
    database.migrate()
    database.migrate()
    with database.connection() as connection:
        assert connection.row_factory is not None
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert names == {"schema_migrations", "settings", "channel_read_model", "operation_log", "processed_event_ids", "import_batches", "streams", "stream_samples", "viewers", "vod_assets", "stream_metric_state", "collection_runs", "viewer_observations", "observation_gaps", "community_state", "community_people", "channel_events", "follow_history", "follower_state", "follower_sync_runs", "follower_sync_pages", "follower_sync_members", "chat_messages", "viewer_streams", "chat_body_deletions", "channel_presets", "stream_notes", "preset_previews", "control_operations", "person_notes", "backup_policy", "backup_jobs", "stream_presence", "eventsub_gaps", "automation_policy", "automation_definitions", "automation_definition_times", "command_aliases", "chat_dispatches", "command_cooldowns", "post_waits", "automation_messages", "prediction_policy", "prediction_presets", "prediction_preset_times", "prediction_cache", "prediction_operations", "restore_jobs"}
    database.quick_check()


def test_schema_has_only_approved_columns_and_migration_failure_rolls_back(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    with database.connection() as connection:
        for table in ("schema_migrations", "settings", "channel_read_model", "operation_log", "processed_event_ids", "import_batches"):
            columns = {row["name"].casefold() for row in connection.execute(f"PRAGMA table_info({table})")}
            assert not any(word in name for name in columns for word in ("token", "secret", "password", "authorization", "credential"))
        indexes = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "processed_event_ids_expires_at_idx" in indexes
    broken = SQLiteDatabase(tmp_path / "broken.sqlite3", migrations=(Migration(1, "broken", ("CREATE TABLE temporary_table(id INTEGER)", "NOT VALID SQL")),))
    with pytest.raises(PersistenceError) as caught:
        broken.migrate()
    assert caught.value.code == "migration_failed"
    with broken.connection() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='temporary_table'").fetchone() is None


def test_migration_clock_failure_rolls_back_all_ddl(tmp_path):
    database = SQLiteDatabase(tmp_path / "clock.sqlite3", clock=lambda: datetime(2026, 1, 1))
    with pytest.raises(PersistenceError) as caught:
        database.migrate()
    assert caught.value.code == "invalid_timestamp"
    with database.connection() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone() is None


def test_connection_initialization_closes_on_pragma_failure(tmp_path, monkeypatch):
    import sqlite3
    class BrokenConnection:
        row_factory = None
        closed = False
        def execute(self, _statement):
            raise sqlite3.OperationalError("synthetic")
        def close(self):
            self.closed = True
    broken = BrokenConnection()
    monkeypatch.setattr(sqlite3, "connect", lambda _path: broken)
    with pytest.raises(PersistenceError) as caught:
        SQLiteDatabase(tmp_path / "core.sqlite3").connect()
    assert caught.value.code == "connection_failed"
    assert broken.closed is True


def test_quick_check_maps_non_ok_and_sqlite_errors(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import sqlite3
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    class Cursor:
        def __init__(self, rows): self._rows = rows
        def fetchall(self): return self._rows
    class Connection:
        def __init__(self, failure=False): self.failure = failure
        def execute(self, _sql):
            if self.failure: raise sqlite3.OperationalError("synthetic")
            return Cursor([("not-ok",)])
    @contextmanager
    def fake_connection():
        yield Connection()
    monkeypatch.setattr(database, "connection", fake_connection)
    with pytest.raises(PersistenceError) as caught: database.quick_check()
    assert caught.value.code == "integrity_check_failed"
    @contextmanager
    def failing_connection():
        yield Connection(True)
    monkeypatch.setattr(database, "connection", failing_connection)
    with pytest.raises(PersistenceError) as caught: database.quick_check()
    assert caught.value.code == "integrity_check_failed"


def test_repositories_use_revisions_detached_json_and_event_deduplication(tmp_path):
    instant = datetime(2026, 1, 2, tzinfo=timezone.utc)
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    settings = SettingsRepository(database)
    assert settings.save(AppSettings(enable_vod_download=True), 0).revision == 1
    with pytest.raises(RevisionConflictError):
        settings.save(AppSettings(), 0)
    channel = ChannelReadModelRepository(database)
    first = channel.put(ChannelReadModel("channel", "title", None, None, ("one",), None, instant, "test"), 0)
    assert first.revision == 1
    second = channel.put(ChannelReadModel("channel", "new", None, None, ("two",), None, instant, "test"), 1)
    assert second.revision == 2 and channel.get("channel").tags == ("two",)
    events = ProcessedEventRepository(database)
    assert events.record_if_new("event", "notification", instant, instant) is True
    assert events.record_if_new("event", "notification", instant, instant) is False
    assert events.prune_expired(instant) == 1


def test_operation_log_rejects_secret_shaped_details_without_echoing_value(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    record = OperationRecord("op", "test", "target", "id", "done", "ok", None, datetime.now(timezone.utc), None, {"access_token": "never-echo"})
    with pytest.raises(PersistenceError) as caught:
        OperationLogRepository(database).append(record)
    assert caught.value.code == "forbidden_secret_key"
    assert "never-echo" not in str(caught.value)


@pytest.mark.parametrize("value", ["/private/state.json", r"C:\private\state.json", r"\\server\share\state.json"])
def test_operation_log_rejects_absolute_paths_without_echoing_them(tmp_path, value):
    database = SQLiteDatabase(tmp_path / "core.sqlite3"); database.migrate()
    record = OperationRecord("op", "test", "target", "id", "done", "ok", None, datetime.now(timezone.utc), None, {"path": value})
    with pytest.raises(PersistenceError) as caught:
        OperationLogRepository(database).append(record)
    assert caught.value.code == "forbidden_absolute_path"
    assert value not in str(caught.value)


def test_operation_log_allows_urls_and_relative_strings(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3"); database.migrate()
    record = OperationRecord("op", "test", "target", "id", "done", "ok", None, datetime.now(timezone.utc), None, {"url": "https://example.invalid/path", "relative": "reports/result.json"})
    assert OperationLogRepository(database).append(record).id == "op"


def test_nested_payloads_are_detached_and_immutable(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    instant = datetime.now(timezone.utc)
    supplied = {"nested": {"items": ["one"]}}
    record = OperationRecord("op", "test", "target", "id", "done", "ok", None, instant, None, supplied)
    supplied["nested"]["items"].append("two")
    stored = OperationLogRepository(database).append(record)
    assert thaw_json(stored.safe_details) == {"nested": {"items": ["one"]}}
    with pytest.raises((AttributeError, TypeError)):
        stored.safe_details.items += ()
    fetched = OperationLogRepository(database).get("op")
    assert thaw_json(fetched.safe_details) == {"nested": {"items": ["one"]}}


def test_import_batch_is_detached_and_rejects_nested_credentials(tmp_path):
    from twitchbot.adapters.persistence.repositories import ImportBatchRepository
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    instant = datetime.now(timezone.utc)
    checksum = "a" * 64
    batch = ImportBatch("batch", "v", instant, instant, {"files": [{"name": "fixtures/nested.json", "size": 1, "checksum": checksum}]}, "source-base", "ok", None)
    assert thaw_json(ImportBatchRepository(database).append(batch).source_manifest) == {"files": [{"name": "fixtures/nested.json", "size": 1, "checksum": checksum}]}
    with pytest.raises(PersistenceError) as caught:
        ImportBatch("bad", "v", instant, instant, {"nested": {"secret_value": "x"}}, "base", "ok", None)
        ImportBatchRepository(database).append(ImportBatch("bad", "v", instant, instant, {"nested": {"secret_value": "x"}}, "base", "ok", None))
    assert caught.value.code == "forbidden_secret_key"


@pytest.mark.parametrize("manifest", [
    {}, {"files": "wrong"}, {"files": [{}]},
    {"files": [{"name": "/private.json", "size": 0, "checksum": "a" * 64}]},
    {"files": [{"name": r"C:\private.json", "size": 0, "checksum": "a" * 64}]},
    {"files": [{"name": r"\\server\share.json", "size": 0, "checksum": "a" * 64}]},
    {"files": [{"name": "../private.json", "size": 0, "checksum": "a" * 64}]},
    {"files": [{"name": "good.json", "size": True, "checksum": "a" * 64}]},
    {"files": [{"name": "good.json", "size": 0, "checksum": "A" * 64}]},
    {"files": [{"name": "good.json", "size": 0, "checksum": "short"}]},
    {"files": [{"name": "good.json", "size": 0, "checksum": "a" * 64, "extra": 1}]},
])
def test_import_manifest_rejects_unsafe_or_invalid_shape(tmp_path, manifest):
    database = SQLiteDatabase(tmp_path / "core.sqlite3"); database.migrate(); now = datetime.now(timezone.utc)
    with pytest.raises(PersistenceError) as caught:
        ImportBatchRepository(database).append(ImportBatch("batch", "v", now, now, manifest, "base", "ok", None))
    assert caught.value.code in {"invalid_import_manifest", "forbidden_absolute_path"}
    assert "private" not in str(caught.value)


def test_import_manifest_allows_empty_file_list(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3"); database.migrate(); now = datetime.now(timezone.utc)
    assert ImportBatchRepository(database).append(ImportBatch("empty", "v", now, now, {"files": []}, "base", "ok", None)).id == "empty"


@pytest.mark.parametrize("migrations", [(object(),), (Migration(True, "x", ("SELECT 1",)),), (Migration(1, " ", ("SELECT 1",)),), (Migration(1, "x", ()),)])
def test_invalid_migration_definitions_fail_closed(tmp_path, migrations):
    with pytest.raises(PersistenceError) as caught:
        SQLiteDatabase(tmp_path / "core.sqlite3", migrations=migrations)
    assert caught.value.code == "invalid_migrations"


def test_migration_history_rejects_future_version(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    with database.connection() as connection:
        connection.execute("UPDATE schema_migrations SET version=? WHERE version=?", (len(MIGRATIONS) + 1, len(MIGRATIONS)))
        connection.commit()
    with pytest.raises(PersistenceError) as caught:
        database.migrate()
    assert caught.value.code == "schema_newer_than_code"


def test_migration_history_rejects_positive_noncontiguous_history(tmp_path):
    migrations = (MIGRATIONS[0], Migration(2, "synthetic_second", ("CREATE TABLE synthetic_second_table(id INTEGER)",)))
    database = SQLiteDatabase(tmp_path / "core.sqlite3", migrations=migrations)
    database.migrate()
    with database.connection() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=1")
        connection.commit()
    with pytest.raises(PersistenceError) as caught:
        database.migrate()
    assert caught.value.code == "invalid_migration_history"


@pytest.mark.parametrize("column", ["name", "checksum"])
def test_migration_history_rejects_drift(tmp_path, column):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    with database.connection() as connection:
        connection.execute(f"UPDATE schema_migrations SET {column}='changed'")
        connection.commit()
    with pytest.raises(PersistenceError) as caught:
        database.migrate()
    assert caught.value.code == "migration_drift"


def test_connection_is_same_thread_only(tmp_path):
    import threading
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    with database.connection() as connection:
        errors = []
        worker = threading.Thread(target=lambda: errors.append(_thread_execute(connection)))
        worker.start(); worker.join()
    assert errors == ["programming_error"]


def _thread_execute(connection):
    import sqlite3
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return "programming_error"
    return "unexpected_success"


def test_settings_corruption_fails_load_and_save_without_replacing_rows(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate()
    repository = SettingsRepository(database)
    repository.save(AppSettings(), 0)
    with database.connection() as connection:
        connection.execute("DELETE FROM settings WHERE key='bot_enabled'")
        before = connection.execute("SELECT key, value_json, revision, updated_at FROM settings ORDER BY key").fetchall()
        connection.commit()
    for action in (repository.load, lambda: repository.save(AppSettings(bot_enabled=True), 1)):
        with pytest.raises(PersistenceError): action()
    with database.connection() as connection:
        after = connection.execute("SELECT key, value_json, revision, updated_at FROM settings ORDER BY key").fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]


@pytest.mark.parametrize("sql", [
    "UPDATE settings SET key='unexpected' WHERE key='bot_enabled'",
    "UPDATE settings SET value_json='not-json' WHERE key='bot_enabled'",
    "UPDATE settings SET revision=9 WHERE key='bot_enabled'",
    "UPDATE settings SET updated_at='2026-01-01T00:00:00Z' WHERE key='bot_enabled'",
])
def test_settings_each_corruption_class_fails_closed(tmp_path, sql):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate(); repository = SettingsRepository(database); repository.save(AppSettings(), 0)
    with database.connection() as connection:
        if "value_json" in sql:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            try:
                connection.execute(sql); connection.commit()
            finally:
                connection.execute("PRAGMA ignore_check_constraints=OFF")
        else:
            connection.execute(sql); connection.commit()
    with pytest.raises(PersistenceError): repository.load()
    with pytest.raises(PersistenceError): repository.save(AppSettings(), 1)


def test_channel_failure_paths_are_typed(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate(); repository = ChannelReadModelRepository(database); instant = datetime.now(timezone.utc)
    model = ChannelReadModel("c", "title", None, None, ("tag",), None, instant, "test")
    assert repository.put(model, 0).revision == 1
    for revision in (-1, True):
        with pytest.raises(PersistenceError): repository.put(model, revision)
    with pytest.raises(TypeError): repository.put(model)  # required optimistic-concurrency argument
    with pytest.raises(RevisionConflictError): repository.put(model, 0)
    bad = ChannelReadModel("bad", "title", None, None, ("tag",), None, instant, "test")
    object.__setattr__(bad, "tags", ["tag"])
    with pytest.raises(PersistenceError): repository.put(bad, 0)
    object.__setattr__(bad, "tags", ("tag", 1))
    with pytest.raises(PersistenceError): repository.put(bad, 0)
    with database.connection() as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        try:
            connection.execute("UPDATE channel_read_model SET tags_json='bad' WHERE channel_id='c'")
            connection.commit()
        finally:
            connection.execute("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(PersistenceError): repository.get("c")


def test_repository_missing_table_and_json_error_paths_are_typed(tmp_path):
    database = SQLiteDatabase(tmp_path / "empty.sqlite3")
    now = datetime.now(timezone.utc)
    with pytest.raises(PersistenceError): OperationLogRepository(database).get("missing")
    with pytest.raises(PersistenceError): OperationLogRepository(database).append(OperationRecord("op", "t", "t", "id", "done", "ok", None, now, None, {}))
    with pytest.raises(PersistenceError): ProcessedEventRepository(database).record_if_new("event", "t", now, now)
    with pytest.raises(PersistenceError): ProcessedEventRepository(database).contains("missing")
    with pytest.raises(PersistenceError): ProcessedEventRepository(database).prune_expired(now)
    with pytest.raises(PersistenceError): ImportBatchRepository(database).get("missing")
    with pytest.raises(PersistenceError): ImportBatchRepository(database).append(ImportBatch("batch", "v", now, now, {"files": []}, "base", "ok", None))
    database = SQLiteDatabase(tmp_path / "core.sqlite3"); database.migrate()
    with pytest.raises(PersistenceError):
        OperationLogRepository(database).append(OperationRecord("nan", "t", "t", "id", "done", "ok", None, now, None, {"n": float("nan")}))
    with pytest.raises(PersistenceError):
        ProcessedEventRepository(database).record_if_new("e", "type", object(), now)
    with pytest.raises(PersistenceError):
        OperationLogRepository(database).append(OperationRecord("bad-time", "t", "t", "id", "done", "ok", None, object(), None, {}))


def test_operation_and_import_duplicate_corrupt_and_detached_reads(tmp_path):
    database = SQLiteDatabase(tmp_path / "core.sqlite3")
    database.migrate(); now = datetime.now(timezone.utc)
    operation = OperationLogRepository(database)
    record = OperationRecord("op", "t", "target", "id", "done", "ok", None, now, None, {"nested": ["one"]})
    operation.append(record)
    with pytest.raises(PersistenceError): operation.append(record)
    assert thaw_json(operation.get("op").safe_details) == {"nested": ["one"]}
    imports = ImportBatchRepository(database)
    batch = ImportBatch("batch", "v", now, now, {"files": [{"name": "fixture.json", "size": 1, "checksum": "b" * 64}]}, "base", "ok", None)
    imports.append(batch)
    with pytest.raises(PersistenceError): imports.append(batch)
    assert thaw_json(imports.get("batch").source_manifest)["files"][0]["name"] == "fixture.json"
    with database.connection() as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        try:
            connection.execute("UPDATE operation_log SET safe_details_json='bad' WHERE id='op'")
            connection.execute("UPDATE import_batches SET source_manifest_json='bad' WHERE id='batch'")
            connection.commit()
        finally:
            connection.execute("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(PersistenceError): operation.get("op")
    with pytest.raises(PersistenceError): imports.get("batch")
