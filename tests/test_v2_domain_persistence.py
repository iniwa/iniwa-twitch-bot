from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from twitchbot.adapters.persistence import (
    SQLiteDatabase, StreamRepository, StreamSampleRepository, ViewerRepository, VodAssetRepository,
)
from twitchbot.application.persistence import PersistenceError, RevisionConflictError, StreamRecord, StreamSample, ViewerRecord, VodAsset, thaw_json


def _db(tmp_path):
    db = SQLiteDatabase(tmp_path / "domain.sqlite3")
    db.migrate()
    return db


def _stream(now):
    return StreamRecord("s1", "channel", "title", None, "game", None, ("tag",), now, None, None, "imported", "full", None, None, None, None, {"unknown": [1]}, None)


def _viewer():
    return ViewerRecord("u1", "login", "Display", None, None, None, None, None, None, None, None, None, None, None, None, 1, None, None, "private", {"legacy": True})


def test_domain_migration_and_stream_samples_are_detached(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo = StreamRepository(db)
    saved = repo.put(_stream(now), 0)
    assert saved.revision == 1 and thaw_json(saved.legacy_metadata) == {"unknown": [1]}
    with pytest.raises(RevisionConflictError): repo.put(_stream(now), 0)
    samples = StreamSampleRepository(db)
    samples.append(StreamSample("s1", now, None, 2, 1.5, None, None, None))
    assert samples.list("s1")[0].viewer_count is None
    with pytest.raises(PersistenceError): samples.append(StreamSample("s1", now, 1, None, None, None, None, None))


def test_viewer_revision_and_safe_metadata(tmp_path):
    db = _db(tmp_path)
    repo = ViewerRepository(db)
    saved = repo.put(_viewer(), 0)
    assert saved.revision == 1 and repo.get("u1").note == "private"
    with pytest.raises(RevisionConflictError): repo.put(_viewer(), 0)
    with pytest.raises(PersistenceError):
        bad = _viewer()
        object.__setattr__(bad, "legacy_metadata", {"access_token": "redacted"})
        repo.put(bad, 1)


def test_domain_updates_retain_creation_timestamp_and_reject_unhashable_state(tmp_path):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    streams = StreamRepository(db)
    first = streams.put(_stream(now), 0)
    updated = streams.put(StreamRecord("s1", "channel", "changed", None, "game", None, (), now, None, None, "imported", "partial", None, None, None, None, {}, None), 1)
    assert updated.revision == 2 and updated.created_at == first.created_at and streams.get("s1").title == "changed"
    with pytest.raises(PersistenceError):
        streams.put(StreamRecord("s2", "channel", "title", None, None, None, (), now, None, None, [], "full", None, None, None, None, {}, None), 0)


@pytest.mark.parametrize("metadata", [{"secret_token": "sentinel"}, {"path": "/private/sentinel"}])
def test_domain_metadata_rejects_secret_and_absolute_path_without_echo(tmp_path, metadata):
    db = _db(tmp_path)
    now = datetime.now(timezone.utc)
    with pytest.raises(PersistenceError) as caught:
        StreamRepository(db).put(StreamRecord("s", "c", "t", None, None, None, (), now, None, None, "imported", "full", None, None, None, None, metadata, None), 0)
    assert "sentinel" not in str(caught.value)


def test_vod_path_is_lexical_and_cascades(tmp_path):
    db = _db(tmp_path)
    StreamRepository(db).put(_stream(datetime.now(timezone.utc)), 0)
    repo = VodAssetRepository(db)
    asset = VodAsset("a1", "s1", "vod", "vods/file.mp4", 10, None, None, "known", "missing")
    assert repo.put(asset, 0).revision == 1
    with pytest.raises(PersistenceError): repo.put(VodAsset("bad", "s1", None, "../x", None, None, None, "x", "x"), 0)
    with db.connection() as c:
        c.execute("DELETE FROM streams WHERE id='s1'"); c.commit()
    assert repo.get("a1") is None


def test_vod_component_sql_allows_filename_with_two_dots(tmp_path):
    db = _db(tmp_path)
    StreamRepository(db).put(_stream(datetime.now(timezone.utc)), 0)
    asset = VodAssetRepository(db).put(VodAsset("a1", "s1", None, "archive/name..part.mp4", None, None, None, "known", "missing"), 0)
    assert asset.relative_path == "archive/name..part.mp4"


def test_migration_0002_schema_contract(tmp_path):
    from twitchbot.adapters.persistence.migrations import MIGRATIONS
    db = SQLiteDatabase(tmp_path / "domain.sqlite3", migrations=MIGRATIONS[:2])
    db.migrate()
    expected = {
        "streams": {"id", "channel_id", "title", "game_id", "game_name", "thumbnail_url", "tags_json", "started_at", "ended_at", "duration_seconds", "source", "completeness", "max_viewers", "average_viewers", "follower_count", "total_comments", "legacy_metadata_json", "import_batch_id", "created_at", "updated_at", "revision"},
        "stream_samples": {"stream_id", "sampled_at", "viewer_count", "chat_count", "messages_per_minute", "bits", "gift_subscriptions", "follower_total"},
        "viewers": {"user_id", "login", "display_name", "followed_at", "unfollowed_at", "visit_count", "watch_seconds", "comment_count", "bits_total", "is_subscriber", "sub_months", "last_sub_at", "last_sub_plan", "gifts_given", "gifts_received", "streak", "last_seen_at", "last_stream_id", "note", "legacy_metadata_json", "created_at", "updated_at", "revision"},
        "vod_assets": {"id", "stream_id", "twitch_vod_id", "relative_path", "size_bytes", "discovered_at", "verified_at", "remote_state", "local_state", "revision"},
    }
    with db.connection() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"schema_migrations", "settings", "channel_read_model", "operation_log", "processed_event_ids", "import_batches", "streams", "stream_samples", "viewers", "vod_assets"}
        for table, columns in expected.items():
            assert {r["name"] for r in c.execute(f"PRAGMA table_info({table})")} == columns
        for index, table, keys in (("streams_started_at_id_idx", "streams", ["started_at", "id"]), ("viewers_login_idx", "viewers", ["login"]), ("viewers_last_seen_idx", "viewers", ["last_seen_at"])):
            assert [r["name"] for r in c.execute(f"PRAGMA index_info({index})")] == keys
        assert [r["name"] for r in c.execute("PRAGMA table_info(stream_samples)") if r["pk"]] == ["stream_id", "sampled_at"]
        assert any(r["table"] == "streams" and r["on_delete"] == "CASCADE" for r in c.execute("PRAGMA foreign_key_list(stream_samples)"))
        assert any(r["table"] == "streams" and r["on_delete"] == "CASCADE" for r in c.execute("PRAGMA foreign_key_list(vod_assets)"))
        assert any(r["unique"] == 1 for r in c.execute("PRAGMA index_list(vod_assets)"))


def test_domain_sql_constraints_reject_invalid_values(tmp_path):
    def fresh(name):
        directory = tmp_path / name
        directory.mkdir()
        return _db(directory)
    stream_sql = "INSERT INTO streams VALUES ('s','c','t',NULL,NULL,NULL,'[]','2026-01-01T00:00:00Z',NULL,NULL,?, ?,NULL,NULL,NULL,NULL,'{}',NULL,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',0)"
    with fresh("source").connection() as c:
        with pytest.raises(sqlite3.IntegrityError): c.execute(stream_sql, ("bad", "full"))
    with fresh("completeness").connection() as c:
        with pytest.raises(sqlite3.IntegrityError): c.execute(stream_sql, ("imported", "bad"))
    with fresh("tags").connection() as c:
        with pytest.raises(sqlite3.IntegrityError): c.execute(stream_sql.replace("'[]'", "'{}'"), ("imported", "full"))
    with fresh("sample").connection() as c:
        c.execute(stream_sql, ("imported", "full"))
        with pytest.raises(sqlite3.IntegrityError): c.execute("INSERT INTO stream_samples VALUES ('s','2026-01-01T00:00:00Z',-1,NULL,NULL,NULL,NULL,NULL)")
    with fresh("viewer_metric").connection() as c:
        with pytest.raises(sqlite3.IntegrityError): c.execute("INSERT INTO viewers VALUES ('u',NULL,NULL,NULL,NULL,-1,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'{}','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',0)")
    with fresh("viewer_bool").connection() as c:
        with pytest.raises(sqlite3.IntegrityError): c.execute("INSERT INTO viewers VALUES ('u',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,2,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'{}','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',0)")
    with fresh("vod_unique").connection() as c:
        c.execute(stream_sql, ("imported", "full")); c.execute("INSERT INTO vod_assets VALUES ('a','s',NULL,'safe/file.mp4',NULL,NULL,NULL,'known','missing',0)")
        with pytest.raises(sqlite3.IntegrityError): c.execute("INSERT INTO vod_assets VALUES ('b','s',NULL,'safe/other.mp4',NULL,NULL,NULL,'known','missing',0)")
    with fresh("vod_path").connection() as c:
        c.execute(stream_sql, ("imported", "full"))
        with pytest.raises(sqlite3.IntegrityError): c.execute("INSERT INTO vod_assets VALUES ('a','s',NULL,'/private',NULL,NULL,NULL,'known','missing',0)")


def _seed_domain(db):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    StreamRepository(db).put(_stream(now), 0)
    StreamSampleRepository(db).append(StreamSample("s1", now, 1, 2, 1.5, 3, 4, 5))
    ViewerRepository(db).put(_viewer(), 0)
    VodAssetRepository(db).put(VodAsset("a1", "s1", "remote", "safe/file.mp4", 1, now, now, "known", "missing"), 0)
    return now


def _corrupt(db, statement, values):
    """Bypass DDL only for a committed synthetic stored-row read test."""
    with db.connection() as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        try:
            connection.execute(statement, values)
            connection.commit()
        finally:
            connection.execute("PRAGMA ignore_check_constraints=OFF")


def _assert_safe_error(action, sentinel):
    with pytest.raises(PersistenceError) as caught:
        action()
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize("value", [None, "", 7, []])
def test_domain_repository_identity_boundaries_fail_before_sql(tmp_path, value):
    db = _db(tmp_path)
    _seed_domain(db)
    actions = (
        lambda: StreamRepository(db).get(value),
        lambda: StreamSampleRepository(db).list(value),
        lambda: ViewerRepository(db).get(value),
        lambda: VodAssetRepository(db).get(value),
        lambda: VodAssetRepository(db).get_by_stream(value),
    )
    for action in actions:
        with pytest.raises(PersistenceError):
            action()


def test_domain_write_identities_and_enums_reject_wrong_and_unhashable_types(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(PersistenceError):
        StreamRepository(db).put(replace(_stream(now), id=[]), 0)
    with pytest.raises(PersistenceError):
        ViewerRepository(db).put(replace(_viewer(), user_id=""), 0)
    with pytest.raises(PersistenceError):
        VodAssetRepository(db).put(VodAsset("", "s1", None, None, None, None, None, "known", "missing"), 0)
    for field, value in (("source", []), ("completeness", {}), ("source", "invalid"), ("completeness", "invalid")):
        with pytest.raises(PersistenceError):
            StreamRepository(db).put(replace(_stream(now), **{field: value}), 0)


@pytest.mark.parametrize("field,value", [
    ("started_at", None), ("started_at", datetime(2026, 1, 1)),
    ("started_at", datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9)))),
    ("started_at", "not-a-time"), ("ended_at", datetime(2026, 1, 1)),
    ("ended_at", datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9)))),
    ("ended_at", object()),
])
def test_stream_required_and_optional_timestamp_input_boundaries(tmp_path, field, value):
    with pytest.raises(PersistenceError):
        StreamRepository(_db(tmp_path)).put(replace(_stream(datetime(2026, 1, 1, tzinfo=timezone.utc)), **{field: value}), 0)


@pytest.mark.parametrize("factory", [
    lambda now: StreamSample("s1", None),
    lambda now: StreamSample("s1", datetime(2026, 1, 1)),
    lambda now: ViewerRecord("u2", None, None, datetime(2026, 1, 1), None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, {}),
    lambda now: VodAsset("a2", "s1", None, None, None, datetime(2026, 1, 1), None, "known", "missing"),
    lambda now: ViewerRecord("u2", None, None, object(), None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, {}),
    lambda now: VodAsset("a2", "s1", None, None, None, object(), None, "known", "missing"),
])
def test_sample_viewer_and_vod_timestamp_input_boundaries(tmp_path, factory):
    db = _db(tmp_path)
    _seed_domain(db)
    item = factory(datetime(2026, 1, 1, tzinfo=timezone.utc))
    repository = StreamSampleRepository(db) if isinstance(item, StreamSample) else ViewerRepository(db) if isinstance(item, ViewerRecord) else VodAssetRepository(db)
    with pytest.raises(PersistenceError):
        repository.append(item) if isinstance(item, StreamSample) else repository.put(item, 0)


@pytest.mark.parametrize("field,value", [
    ("duration_seconds", True), ("max_viewers", -1), ("average_viewers", float("nan")),
    ("average_viewers", float("inf")), ("viewer_count", True), ("messages_per_minute", -0.1),
    ("visit_count", True), ("bits_total", -1), ("size_bytes", float("inf")),
])
def test_domain_numeric_input_boundaries(tmp_path, field, value):
    db = _db(tmp_path)
    now = _seed_domain(db)
    if field in {"duration_seconds", "max_viewers", "average_viewers"}:
        action = lambda: StreamRepository(db).put(replace(_stream(now), id="s2", **{field: value}), 0)
    elif field in {"viewer_count", "messages_per_minute"}:
        action = lambda: StreamSampleRepository(db).append(replace(StreamSample("s1", now), **{field: value}))
    elif field in {"visit_count", "bits_total"}:
        action = lambda: ViewerRepository(db).put(replace(_viewer(), user_id="u2", **{field: value}), 0)
    else:
        action = lambda: VodAssetRepository(db).put(VodAsset("a2", "s1", None, None, value, None, None, "known", "missing"), 0)
    with pytest.raises(PersistenceError):
        action()


def test_tags_and_metadata_are_validated_detached_and_redacted(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for tags in ([], ["tag"], ("tag", 1)):
        record = _stream(now)
        object.__setattr__(record, "tags", tags)
        with pytest.raises(PersistenceError):
            StreamRepository(db).put(record, 0)
    supplied = {"nested": {"items": ["safe"]}}
    record = replace(_stream(now), legacy_metadata=supplied)
    supplied["nested"]["items"].append("later")
    saved = StreamRepository(db).put(record, 0)
    assert thaw_json(saved.legacy_metadata) == {"nested": {"items": ["safe"]}}
    for metadata, sentinel in (({"nested": {"access_token": "secret-sentinel"}}, "secret-sentinel"), ({"nested": ["/private/path-sentinel"]}, "path-sentinel")):
        _assert_safe_error(lambda: StreamRepository(db).put(replace(_stream(now), id="s2", legacy_metadata=metadata), 0), sentinel)


def test_domain_metadata_rejects_wrong_containers_directly(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stream = _stream(now)
    viewer = _viewer()
    object.__setattr__(stream, "legacy_metadata", ["not-an-object"])
    object.__setattr__(viewer, "legacy_metadata", ["not-an-object"])
    with pytest.raises(PersistenceError):
        StreamRepository(db).put(stream, 0)
    with pytest.raises(PersistenceError):
        ViewerRepository(db).put(viewer, 0)


def test_viewer_and_vod_optional_timestamps_reject_non_utc_values(tmp_path):
    db = _db(tmp_path)
    _seed_domain(db)
    non_utc = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9)))
    with pytest.raises(PersistenceError):
        ViewerRepository(db).put(replace(_viewer(), user_id="u2", followed_at=non_utc), 0)
    with pytest.raises(PersistenceError):
        VodAssetRepository(db).put(VodAsset("a2", "s1", None, None, None, non_utc, None, "known", "missing"), 0)


@pytest.mark.parametrize("path", ["archive/name..part.mp4", r"folder\file.mp4", "/absolute.mp4", r"C:\drive.mp4", r"\\host\share.mp4", "", ".", "..", "a//b", "a/./b", "a/../b"])
def test_vod_repository_lexical_path_matrix(tmp_path, path):
    db = _db(tmp_path)
    _seed_domain(db)
    asset = VodAsset("a1", "s1", None, path, None, None, None, "known", "missing")
    if path == "archive/name..part.mp4":
        assert VodAssetRepository(db).put(asset, 1).relative_path == path
    else:
        with pytest.raises(PersistenceError):
            VodAssetRepository(db).put(asset, 0)


def test_vod_identity_states_optional_id_and_update_collisions(tmp_path):
    db = _db(tmp_path)
    _seed_domain(db)
    repo = VodAssetRepository(db)
    for asset in (
        VodAsset("a2", "s1", 3, None, None, None, None, "known", "missing"),
        VodAsset("a2", "s1", None, None, None, None, None, "", "missing"),
        VodAsset("a2", "s1", None, None, None, None, None, "known", []),
        VodAsset("a2", "missing", None, None, None, None, None, "known", "missing"),
        VodAsset("a1", "s1", None, None, None, None, None, "known", "missing"),
    ):
        with pytest.raises(PersistenceError):
            repo.put(asset, 0)
    StreamRepository(db).put(replace(_stream(datetime(2026, 1, 1, tzinfo=timezone.utc)), id="s2"), 0)
    repo.put(VodAsset("a2", "s2", None, None, None, None, None, "known", "missing"), 0)
    with pytest.raises(PersistenceError):
        repo.put(VodAsset("a2", "s1", None, None, None, None, None, "known", "missing"), 1)


def test_domain_update_revisions_created_at_and_duplicate_samples(tmp_path):
    db = _db(tmp_path)
    now = _seed_domain(db)
    streams = StreamRepository(db); viewers = ViewerRepository(db); vods = VodAssetRepository(db)
    first_stream = streams.get("s1"); first_viewer = viewers.get("u1"); first_vod = vods.get("a1")
    assert streams.put(replace(first_stream, title="changed"), 1).created_at == first_stream.created_at
    assert viewers.put(replace(first_viewer, login="changed"), 1).created_at == first_viewer.created_at
    assert vods.put(replace(first_vod, local_state="present"), 1).revision == 2
    with pytest.raises(PersistenceError): StreamSampleRepository(db).append(StreamSample("s1", now))
    with pytest.raises(PersistenceError): StreamSampleRepository(db).append(StreamSample("missing", now))


@pytest.mark.parametrize("table,column,value,reader", [
    ("streams", "tags_json", "{\"bad\":\"stream-tags-sentinel\"}", lambda db: StreamRepository(db).get("s1")),
    ("streams", "legacy_metadata_json", "[\"stream-metadata-sentinel\"]", lambda db: StreamRepository(db).get("s1")),
    ("streams", "started_at", "stream-time-sentinel", lambda db: StreamRepository(db).get("s1")),
    ("streams", "created_at", "stream-audit-sentinel", lambda db: StreamRepository(db).get("s1")),
    ("streams", "source", "stream-source-sentinel", lambda db: StreamRepository(db).get("s1")),
    ("streams", "completeness", "stream-completeness-sentinel", lambda db: StreamRepository(db).get("s1")),
    ("streams", "max_viewers", -1, lambda db: StreamRepository(db).get("s1")),
    ("streams", "revision", -1, lambda db: StreamRepository(db).get("s1")),
    ("stream_samples", "sampled_at", "sample-time-sentinel", lambda db: StreamSampleRepository(db).list("s1")),
    ("stream_samples", "viewer_count", -1, lambda db: StreamSampleRepository(db).list("s1")),
    ("stream_samples", "chat_count", "sample-count-sentinel", lambda db: StreamSampleRepository(db).list("s1")),
    ("stream_samples", "messages_per_minute", -1.5, lambda db: StreamSampleRepository(db).list("s1")),
    ("viewers", "legacy_metadata_json", "[\"viewer-metadata-sentinel\"]", lambda db: ViewerRepository(db).get("u1")),
    ("viewers", "followed_at", "viewer-time-sentinel", lambda db: ViewerRepository(db).get("u1")),
    ("viewers", "created_at", "viewer-audit-sentinel", lambda db: ViewerRepository(db).get("u1")),
    ("viewers", "visit_count", -1, lambda db: ViewerRepository(db).get("u1")),
    ("viewers", "is_subscriber", 2, lambda db: ViewerRepository(db).get("u1")),
    ("viewers", "revision", -1, lambda db: ViewerRepository(db).get("u1")),
    ("vod_assets", "relative_path", "../vod-path-sentinel", lambda db: VodAssetRepository(db).get("a1")),
    ("vod_assets", "discovered_at", "vod-time-sentinel", lambda db: VodAssetRepository(db).get_by_stream("s1")),
    ("vod_assets", "verified_at", "vod-verified-sentinel", lambda db: VodAssetRepository(db).get("a1")),
    ("vod_assets", "size_bytes", -1, lambda db: VodAssetRepository(db).get("a1")),
    ("vod_assets", "remote_state", "", lambda db: VodAssetRepository(db).get_by_stream("s1")),
    ("vod_assets", "local_state", "", lambda db: VodAssetRepository(db).get("a1")),
    ("vod_assets", "revision", -1, lambda db: VodAssetRepository(db).get("a1")),
])
def test_stored_domain_rows_fail_closed_and_redact(tmp_path, table, column, value, reader):
    db = _db(tmp_path)
    _seed_domain(db)
    _corrupt(db, f"UPDATE {table} SET {column}=?", (value,))
    if isinstance(value, str) and value:
        _assert_safe_error(lambda: reader(db), value)
    else:
        with pytest.raises(PersistenceError):
            reader(db)


def test_sqlite_nonfinite_storage_limit_is_covered_by_repository_input(tmp_path):
    # SQLite stores IEEE NaN as NULL through the Python driver, so direct DDL cannot distinguish it.
    db = _db(tmp_path)
    _seed_domain(db)
    with pytest.raises(PersistenceError):
        StreamSampleRepository(db).append(StreamSample("s1", datetime(2026, 1, 2, tzinfo=timezone.utc), messages_per_minute=float("nan")))


def test_raw_vod_sql_rejects_single_backslash_and_accepts_safe_dotted_path(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    StreamRepository(db).put(_stream(now), 0)
    with db.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO vod_assets VALUES ('a1','s1',NULL,?,NULL,NULL,NULL,'known','missing',0)", (r"folder\file.mp4",))
        connection.execute("INSERT INTO vod_assets VALUES ('a2','s1',NULL,?,NULL,NULL,NULL,'known','missing',0)", ("archive/name..part.mp4",))
        connection.commit()
