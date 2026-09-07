from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from twitchbot.adapters.persistence.sqlite import SQLiteDatabase
from twitchbot.migration import CandidateImportError, CandidateImporter, LegacySourceInspector


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")


def _fixture(tmp_path):
    source, downloads, candidate = tmp_path / "source", tmp_path / "downloads", tmp_path / "candidate.sqlite3"
    source.mkdir(parents=True); downloads.mkdir(); (downloads / "vod.mp4").write_bytes(b"not-read")
    _write(source / "config.json", {"broadcaster_id": "channel"})
    _write(source / "history/stream_index.json", {"stream": {"title": "title", "start_time": "2026-01-01T00:00:00+09:00", "duration": "01:02", "max_viewers": 3, "file_path": "vod.mp4", "vod_id": "vod"}})
    _write(source / "history/stream_stream.jsonl", '{"timestamp":"2026-01-01T00:01:00Z","stream_info":{"follower_total":2,"tags":["tag"]},"metrics":{"viewer_count":1,"chat_count":2,"bits":3,"gift_subs":4}}\nnot-json\n')
    _write(source / "viewers.json", {"viewer": {"name":"private", "login":"login", "total_visits": 1, "last_seen_ts": 0, "memo":"private-note"}})
    database=SQLiteDatabase(candidate); database.migrate()
    inspector=LegacySourceInspector(source, downloads, "fixture", clock=lambda: datetime(2026,1,2,tzinfo=timezone.utc), monotonic=lambda: 1.0)
    return source, downloads, database, inspector.inspect()


def test_legacy_follow_dates_preserve_day_precision_and_optional_empty_values(tmp_path):
    source,downloads,database,_=_fixture(tmp_path)
    _write(source/'viewers.json',{'viewer':{'name':'person','followed_at':'2026-09-01','unfollowed_at':'','last_seen_ts':1788220800,'is_follower':True}})
    report=LegacySourceInspector(source,downloads,'fixture').inspect()
    assert not any(i[2]=='viewer' for i in report.issues)
    importer=CandidateImporter(source,downloads,'fixture',database)
    result=importer.import_report(report)
    assert dict(result.deferred_counts)['viewers:date_only_preserved']==1
    with database.connection() as c:
        row=c.execute('SELECT followed_at,unfollowed_at,legacy_metadata_json,last_seen_at FROM viewers').fetchone()
        assert row[0] is None and row[1] is None
        assert json.loads(row[2])=={'date_only':{'followed_at':'2026-09-01'},'is_follower':True}
        assert row[3]=='2026-09-01T00:00:00.000000Z'
        c.execute("UPDATE viewers SET legacy_metadata_json='{}'");c.commit()
    with pytest.raises(CandidateImportError,match='candidate_verification_failed'):importer.verify_import(report)


@pytest.mark.parametrize('value',['2026-02-30','2026-09-01T12:00:00','invalid'])
def test_invalid_or_ambiguous_viewer_dates_stay_deferred(tmp_path,value):
    source,downloads,database,_=_fixture(tmp_path)
    _write(source/'viewers.json',{'viewer':{'followed_at':value}})
    report=LegacySourceInspector(source,downloads,'fixture').inspect()
    result=CandidateImporter(source,downloads,'fixture',database).import_report(report)
    assert dict(result.deferred_counts)['viewers:invalid']==1
    assert dict(result.aggregates)['viewers']==0


def test_unknown_fields_and_naive_sample_times_remain_visible_in_report(tmp_path):
    source,downloads,database,_=_fixture(tmp_path)
    _write(source/'config.json',{'broadcaster_id':'channel','unmapped_option':True,'presets':[{'name':'keep'}]})
    _write(source/'history/stream_stream.jsonl','{"timestamp":"2026-09-01T12:00:00","stream_info":{},"metrics":{},"messages":[{"time":"12:00","user":"fixture","text":"saved","is_sub":false,"badges":""}]}\n')
    report=LegacySourceInspector(source,downloads,'fixture').inspect()
    result=CandidateImporter(source,downloads,'fixture',database).import_report(report)
    assert dict(result.deferred_counts)['samples:timezone_missing']==1
    assert dict(result.deferred_counts)['unknown:config:unmapped_option']==1
    assert dict(result.deferred_counts)['configuration:presets']==1
    assert dict(result.deferred_counts)['legacy_activity:messages']==1


def test_candidate_import_is_atomic_safe_and_verified_noop(tmp_path):
    source, downloads, database, report = _fixture(tmp_path)
    importer=CandidateImporter(source, downloads, "fixture", database, clock=lambda: datetime(2026,1,2,tzinfo=timezone.utc))
    outcome=importer.import_report(report)
    assert outcome.result == "completed"
    safe=outcome.to_safe_mapping(); assert safe["aggregates"]["streams"] == 1 and safe["aggregates"]["samples"] == 1
    assert "private-note" not in repr(outcome) and "private" not in repr(safe)
    assert importer.verify_import(report).result == "verified"
    assert importer.import_report(report).result == "no_op"
    with database.connection() as c:
        assert c.execute("select count(*) from import_batches").fetchone()[0] == 1
        assert tuple(c.execute("select started_at,game_name,duration_seconds from streams").fetchone()) == ("2025-12-31T15:00:00.000000Z", None, 62)
        c.execute("UPDATE stream_samples SET viewer_count=99"); c.commit()
    with pytest.raises(CandidateImportError) as changed:
        importer.verify_import(report)
    assert changed.value.code == "candidate_verification_failed"


def test_source_change_and_credentials_leave_candidate_empty(tmp_path):
    source, downloads, database, report = _fixture(tmp_path)
    _write(source / "config.json", {"broadcaster_id":"channel", "access_token":"SENTINEL"})
    importer=CandidateImporter(source, downloads, "fixture", database)
    with pytest.raises(CandidateImportError) as caught: importer.import_report(report)
    assert caught.value.code in {"source_changed", "report_mismatch"}
    with database.connection() as c: assert c.execute("select count(*) from import_batches").fetchone()[0] == 0
    report=LegacySourceInspector(source,downloads,"fixture").inspect()
    with pytest.raises(CandidateImportError) as caught: importer.import_report(report)
    assert caught.value.code == "credential_validation_required" and "SENTINEL" not in str(caught.value)


def test_invalid_candidate_schema_is_safe_and_default_is_rejected(tmp_path):
    source, downloads, _, report = _fixture(tmp_path)
    empty=SQLiteDatabase(tmp_path / "empty.sqlite3")
    importer=CandidateImporter(source,downloads,"fixture",empty)
    with pytest.raises(CandidateImportError) as caught: importer.import_report(report)
    assert caught.value.code == "candidate_schema_invalid"


def test_accepted_cutoff_produces_noop_with_advancing_importer_clock(tmp_path):
    source, downloads, database, report = _fixture(tmp_path)
    ticks=iter(datetime(2026, 1, day, tzinfo=timezone.utc) for day in range(3, 20))
    importer=CandidateImporter(source, downloads, "fixture", database, clock=lambda: next(ticks))
    first=importer.import_report(report)
    assert importer.import_report(report).result == "no_op"
    assert first.batch_id == importer.verify_import(report).batch_id


def test_duplicate_or_injected_failure_and_source_mutation_roll_back(tmp_path, monkeypatch):
    source, downloads, database, report = _fixture(tmp_path)
    _write(source / "history/stream_stream.jsonl", '{"timestamp":"2026-01-01T00:01:00Z","stream_info":{},"metrics":{}}\n' * 2)
    report=LegacySourceInspector(source,downloads,"fixture").inspect()
    importer=CandidateImporter(source,downloads,"fixture",database)
    with pytest.raises(CandidateImportError) as duplicate: importer.import_report(report)
    assert duplicate.value.code == "candidate_write_failed"
    with database.connection() as c: assert c.execute("select count(*) from import_batches").fetchone()[0] == 0


def test_source_mutation_during_aggregate_rolls_back_before_commit(tmp_path, monkeypatch):
    source, downloads, database, report = _fixture(tmp_path)
    importer=CandidateImporter(source,downloads,"fixture",database)
    original=importer._aggregate
    def aggregate_then_mutate(connection):
        result=original(connection)
        _write(source / "config.json", {"broadcaster_id":"changed"})
        return result
    monkeypatch.setattr(importer, "_aggregate", aggregate_then_mutate)
    with pytest.raises(CandidateImportError) as changed: importer.import_report(report)
    assert changed.value.code == "source_changed"
    with database.connection() as c:
        assert [c.execute(f"select count(*) from {table}").fetchone()[0] for table in ("import_batches", "streams", "stream_samples", "viewers", "vod_assets")] == [0, 0, 0, 0, 0]

    source, downloads, database, report = _fixture(tmp_path / "injected")
    importer=CandidateImporter(source,downloads,"fixture",database)
    monkeypatch.setattr(importer, "_aggregate", staticmethod(lambda _connection: (_ for _ in ()).throw(sqlite3.Error())))
    with pytest.raises(CandidateImportError) as injected: importer.import_report(report)
    assert injected.value.code == "candidate_write_failed"
    with database.connection() as c: assert c.execute("select count(*) from streams").fetchone()[0] == 0

    source, downloads, database, report = _fixture(tmp_path / "mutated")
    importer=CandidateImporter(source,downloads,"fixture",database); original=importer._inspector.verify_unchanged; calls=0
    def mutate_on_final(value):
        nonlocal calls
        calls += 1
        if calls == 2: _write(source / "config.json", {"broadcaster_id":"changed"})
        return original(value)
    monkeypatch.setattr(importer._inspector, "verify_unchanged", mutate_on_final)
    with pytest.raises(CandidateImportError) as changed: importer.import_report(report)
    assert changed.value.code == "source_changed"
    with database.connection() as c: assert c.execute("select count(*) from import_batches").fetchone()[0] == 0


def test_unsafe_vod_candidate_conflict_and_schema_shape_are_safe(tmp_path, monkeypatch):
    source, downloads, database, report = _fixture(tmp_path)
    _write(source / "history/stream_index.json", {"stream":{"title":"title","start_time":"2026-01-01T00:00:00Z","file_path":"../SENTINEL_PATH","vod_status":"downloaded"}})
    report=LegacySourceInspector(source,downloads,"fixture").inspect(); importer=CandidateImporter(source,downloads,"fixture",database)
    original_open=Path.open
    def no_media(path, *args, **kwargs):
        if path == downloads / "vod.mp4": raise AssertionError("media read")
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",no_media)
    outcome=importer.import_report(report)
    assert "SENTINEL_PATH" not in repr(outcome) and outcome.to_safe_mapping()["vod_path_counts"]["traversal"] == 1
    with database.connection() as c: assert c.execute("select relative_path from vod_assets").fetchone()[0] is None

    source, downloads, database, report = _fixture(tmp_path / "broken")
    with database.connection() as c: c.execute("DROP TABLE viewers"); c.execute("CREATE TABLE viewers(x TEXT)"); c.commit()
    with pytest.raises(CandidateImportError) as malformed: CandidateImporter(source,downloads,"fixture",database).import_report(report)
    assert malformed.value.code == "candidate_schema_invalid"


def test_nonempty_candidate_is_refused_without_overwrite(tmp_path):
    source, downloads, database, report = _fixture(tmp_path)
    with database.connection() as c:
        c.execute("INSERT INTO import_batches VALUES (?,?,?,?,?,?,?,?)", ("other", "v", "2026-01-01T00:00:00.000000Z", "2026-01-01T00:00:00.000000Z", '{"files":[]}', "safe", "completed", None)); c.commit()
    with pytest.raises(CandidateImportError) as caught:
        CandidateImporter(source,downloads,"fixture",database).import_report(report)
    assert caught.value.code == "candidate_not_empty"
