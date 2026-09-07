from datetime import datetime, timedelta, timezone
import json
import socket

import pytest

from twitchbot.application.analytics import CollectionRun, ObservationGap, ViewerObservation, calculate_viewers, graph_intervals
from twitchbot.application.persistence import PersistenceError, StreamRecord
from twitchbot.adapters.persistence import SQLiteDatabase, StreamRepository
from twitchbot.adapters.persistence.analytics import AnalyticsRepository, HistoryReader
from twitchbot.adapters.persistence.migrations import MIGRATIONS
from twitchbot.adapters.persistence.sqlite import to_rfc3339

BASE = datetime(2026, 9, 5, 14, 59, 40, tzinfo=timezone.utc)  # crosses JST midnight


def at(seconds):
    return BASE + timedelta(seconds=seconds)


@pytest.mark.parametrize("points,end,start,gap,weighted,covered,average,maximum", [
    ([(0,10),(20,20),(40,30)],60,0,None,1200,60,20,30),
    ([(0,10),(10,30),(40,20)],60,0,None,1400,60,1400/60,30),
    ([(0,10),(20,20),(120,30)],140,0,None,1400,70,20,30),
    ([(0,10),(20,20),(120,30)],140,0,(40,120),1200,60,20,30),
    ([(0,0),(20,0)],40,0,None,0,40,0,0),
    ([],60,0,None,0,0,None,None),
    ([(20,10),(40,20)],60,0,None,600,40,15,20),
    ([(0,10),(20,20),(40,30)],50,10,None,800,40,20,30),
    ([(0,10),(20,30)],40,0,(10,20),700,30,700/30,30),
])
def test_bounded_hold_examples(points,end,start,gap,weighted,covered,average,maximum):
    result = calculate_viewers([CollectionRun("r",at(0))],
                               [ViewerObservation("r",at(t),v) for t,v in points],
                               [ObservationGap("g",at(gap[0]),at(gap[1]),"disconnected")] if gap else [], at(start),at(end))
    assert result.weighted_viewer_seconds == weighted
    assert result.covered_seconds == covered
    assert result.average_viewers == pytest.approx(average) if average is not None else result.average_viewers is None
    assert result.max_viewers == maximum
    assert result.coverage_ratio == pytest.approx(covered/(end-start))


def test_restart_midnight_and_live_endpoint_do_not_backfill():
    runs = [CollectionRun("one",at(0),at(10)),CollectionRun("two",at(15))]
    observations = [ViewerObservation("one",at(0),10),ViewerObservation("two",at(20),30)]
    result = calculate_viewers(runs,observations,[],at(0),at(40))
    assert (result.weighted_viewer_seconds,result.covered_seconds) == (700,30)
    assert result.segments[0].end == at(10)
    assert result.segments[1].start == at(20)
    endpoint = calculate_viewers([CollectionRun("r",at(0))],[ViewerObservation("r",at(0),7)],[],at(0),at(0),include_endpoint=True)
    assert (endpoint.average_viewers,endpoint.coverage_ratio,endpoint.max_viewers) == (None,None,7)
    historical = calculate_viewers([CollectionRun("r",at(0))],[ViewerObservation("r",at(40),99)],[],at(0),at(40))
    assert historical.max_viewers is None


def test_gap_clips_carry_and_unknown_duration_preserves_observed_average():
    result = calculate_viewers([CollectionRun("r",at(0))],[ViewerObservation("r",at(0),10)],
                               [ObservationGap("g",at(5),at(10),"request_failed")],at(0),at(40),duration_known=False)
    assert result.covered_seconds == 5
    assert result.average_viewers == 10
    assert result.coverage_ratio is None


@pytest.mark.parametrize("value", [-1,True,1.5,float("nan"),2**63])
def test_invalid_counts_are_rejected(value):
    with pytest.raises(PersistenceError):
        ViewerObservation("r",at(0),value)


def test_invalid_times_and_overlaps_are_rejected():
    with pytest.raises(PersistenceError):
        CollectionRun("r",datetime(2026,1,1))
    with pytest.raises(PersistenceError):
        calculate_viewers([CollectionRun("a",at(0)),CollectionRun("b",at(10))],[],[],at(0),at(40))
    with pytest.raises(PersistenceError):
        calculate_viewers([],[],[ObservationGap("a",at(0),at(20),"unknown"),ObservationGap("b",at(10),at(30),"unknown")],at(0),at(40))


def stream(sid="s1", seconds=60):
    return StreamRecord(sid,"channel","架空の配信",None,"ゲーム",None,(),at(0),at(seconds) if seconds else None,seconds,
                        "bot","partial",99,88,None,None,{},None)


@pytest.fixture
def store(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError("external access forbidden")
    monkeypatch.setattr(socket,"create_connection",forbidden)
    db=SQLiteDatabase(tmp_path / "fixture.sqlite3")
    db.migrate()
    StreamRepository(db).put(stream(),0)
    repo=AnalyticsRepository(db)
    repo.start_run("s1",CollectionRun("r",at(0)))
    for t,v in [(0,10),(20,20),(40,30)]:
        repo.append("s1",ViewerObservation("r",at(t),v))
    repo.set_end_precision("s1","confirmed")
    return db,repo,HistoryReader(db,clock=lambda:at(200))


def test_migration_upgrade_preserves_old_schema_identity_and_data(tmp_path):
    old=SQLiteDatabase(tmp_path / "upgrade.sqlite3",migrations=MIGRATIONS[:2])
    old.migrate()
    StreamRepository(old).put(stream(),0)
    with old.connection() as c:
        before=[tuple(r) for r in c.execute("SELECT * FROM schema_migrations")]
    new=SQLiteDatabase(old.path)
    new.migrate()
    new.migrate()
    with new.connection() as c:
        assert [tuple(r) for r in c.execute("SELECT * FROM schema_migrations WHERE version<3")] == before
        assert c.execute("SELECT average_viewers FROM streams").fetchone()[0] == 88
        assert c.execute("SELECT COUNT(*) FROM viewer_observations").fetchone()[0] == 0


def test_repository_idempotence_revisions_and_sanitized_conflicts(store):
    db,repo,reader=store
    before=reader.detail("s1")
    assert repo.append("s1",ViewerObservation("r",at(20),20)) is False
    assert reader.detail("s1")["data_revision"] == before["data_revision"]
    with pytest.raises(PersistenceError):
        repo.append("s1",ViewerObservation("r",at(20),999))
    assert reader.detail("s1")["data_revision"] == before["data_revision"]
    assert repo.stop_run("s1","r",at(50)) is True
    after=reader.detail("s1")
    assert after["covered_seconds"] == 50 and after["average_viewers"] == 18
    assert after["data_revision"] != before["data_revision"]
    assert repo.stop_run("s1","r",at(50)) is False
    with pytest.raises(PersistenceError):
        repo.append("s1",ViewerObservation("r",at(51),1))
    with pytest.raises(PersistenceError):
        repo.start_run("s1",CollectionRun("overlap",at(45)))
    repo.start_run("s1",CollectionRun("next",at(55)))
    repo.append("s1",ViewerObservation("next",at(55),0))
    assert reader.detail("s1")["covered_seconds"] == 55


def test_gaps_close_atomically_and_cannot_cover_valid_observations(store):
    db,repo,reader=store
    with pytest.raises(PersistenceError):
        repo.save_gap("s1",ObservationGap("bad",at(19),at(21),"request_failed"))
    repo.save_gap("s1",ObservationGap("g",at(45),None,"disconnected"))
    revision=reader.detail("s1")["data_revision"]
    with pytest.raises(PersistenceError):
        repo.append("s1",ViewerObservation("r",at(50),0))
    assert reader.detail("s1")["data_revision"] == revision
    repo.save_gap("s1",ObservationGap("g",at(45),at(55),"disconnected"))
    repo.append("s1",ViewerObservation("r",at(55),0))
    assert reader.detail("s1")["covered_seconds"] == 50


def test_history_snapshot_range_legacy_and_comparison(store):
    db,repo,reader=store
    detail=reader.detail("s1",start=at(10),end=at(50))
    assert detail["average_viewers"] == 20 and detail["covered_seconds"] == 40
    assert detail["legacy_metrics"]["average_viewers"] == 88
    StreamRepository(db).put(stream("s2",40),0)
    repo.start_run("s2",CollectionRun("r",at(0)))
    repo.append("s2",ViewerObservation("r",at(0),0))
    repo.append("s2",ViewerObservation("r",at(20),0))
    repo.set_end_precision("s2","confirmed")
    result=reader.compare(["s2","s1"],scope="common")
    assert result["average_difference"] == 15
    assert result["average_change_ratio"] is None
    assert {i["as_of"] for i in result["items"]} == {result["as_of"]}
    assert {i["range_end"] for i in result["items"]} == {to_rfc3339(at(40))}
    first=reader.list_streams(limit=1)
    second=reader.list_streams(limit=1,before=first["next_cursor"])
    assert [first["items"][0]["id"],second["items"][0]["id"]] == ["s2","s1"]
    assert second["next_cursor"] is None


def test_history_metric_sort_uses_new_summary_before_paging_and_nulls_last(store):
    db, repo, reader = store
    StreamRepository(db).put(stream('s2', 60), 0)
    repo.start_run('s2', CollectionRun('r2', at(0)))
    repo.append('s2', ViewerObservation('r2', at(0), 100))
    repo.append('s2', ViewerObservation('r2', at(20), 100))
    repo.append('s2', ViewerObservation('r2', at(40), 100))
    repo.set_end_precision('s2', 'confirmed')
    StreamRepository(db).put(stream('missing', 60), 0)
    descending = reader.list_streams(sort='average_viewers', order='desc')
    ascending = reader.list_streams(sort='average_viewers', order='asc')
    assert [item['id'] for item in descending['items']] == ['s2', 's1', 'missing']
    assert [item['id'] for item in ascending['items']] == ['s1', 's2', 'missing']
    assert descending['items'][-1]['average_viewers'] is None
    first = reader.list_streams(limit=1, sort='max_viewers', order='desc')
    second = reader.list_streams(limit=1, before=first['next_cursor'], sort='max_viewers', order='desc')
    assert [first['items'][0]['id'], second['items'][0]['id']] == ['s2', 's1']
    repo.append('s1', ViewerObservation('r', at(50), 40))
    with pytest.raises(PersistenceError, match='list_changed'):
        reader.list_streams(limit=1, before=first['next_cursor'], sort='max_viewers', order='desc')
    from twitchbot.container import Container
    from twitchbot.web.app import create_app
    token = json.dumps(first['next_cursor'], separators=(',', ':'))
    assert create_app(Container(history_reader=reader)).test_client().get('/api/v2/streams', query_string={
        'limit': 1, 'sort': 'max_viewers', 'order': 'desc', 'cursor': token}).status_code == 409


def test_readonly_missing_database_and_bounded_errors(tmp_path,store):
    missing=SQLiteDatabase(tmp_path / "missing.sqlite3")
    with pytest.raises(PersistenceError):
        HistoryReader(missing).list_streams()
    assert not missing.path.exists()
    db,repo,reader=store
    with reader._read() as c:
        import sqlite3
        with pytest.raises(sqlite3.OperationalError):
            c.execute("DELETE FROM streams")
    reader.MAX_ROWS=1
    with pytest.raises(PersistenceError,match="history_limit_exceeded"):
        reader.detail("s1")


def test_history_api_pages_and_default_inert_boundary(store,monkeypatch):
    from twitchbot.container import Container
    from twitchbot.web.app import create_app
    db,repo,reader=store
    app=create_app(Container(history_reader=reader))
    app.testing=True
    client=app.test_client()
    revision=reader.detail("s1")["data_revision"]
    # GET must never invoke the writable connection or migrate the candidate.
    monkeypatch.setattr(db,"connect",lambda:pytest.fail("write connection used by GET"))
    for url in ("/api/v2/streams","/api/v2/streams/s1/analytics","/v2/history","/v2/history/s1"):
        response=client.get(url)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        if url.startswith("/v2/"):
            assert '/v2-static/v2/app.js' in response.text
            assert '<main data-page="history-' in response.text
    assert client.get("/api/v2/streams/nope/analytics").status_code == 404
    for suffix in ("?limit=0","?cursor=oops","?cursor=3","?limit=201","?sort=average_viewers%20DESC","?order=asc&order=desc"):
        assert client.get("/api/v2/streams"+suffix).status_code == 400
    sorted_page = client.get('/v2/history?sort=average_viewers&order=desc')
    assert sorted_page.status_code == 200 and 'aria-sort="descending"' in sorted_page.text
    assert client.get("/api/v2/stream-comparisons?id=s1&id=s1").status_code == 400
    assert client.get("/api/v2/streams/s1/analytics?start=bad").status_code == 400
    assert reader.detail("s1")["data_revision"] == revision
    default=create_app().test_client()
    assert default.get("/api/v2/streams").status_code == 503
    assert "保存先" in default.get("/v2/history").text


def test_partial_navigation_uses_bounded_memory_cache_and_explicit_modules(store):
    from twitchbot.container import Container
    from twitchbot.web.app import create_app
    db,repo,reader=store
    client=create_app(Container(history_reader=reader)).test_client()
    asset=client.get("/v2-static/v2/app.js")
    assert asset.status_code == 200
    source=asset.get_data(as_text=True)
    assert "MAX_PAGES=5" in source and "MAX_BYTES=2*1024*1024" in source and "FRESH_MS=60_000" in source
    assert 'new Set(["app.css","community.css","control.css","automation.css"])' in source
    assert "localStorage" not in source and "sessionStorage" not in source
    assert "eval(" not in source and "serviceWorker" not in source
    assert 'modules.has("live")&&modules.has("presets")' in source


def test_no_observations_does_not_present_legacy_average_as_new(store):
    db,repo,reader=store
    StreamRepository(db).put(stream("old",40),0)
    result=reader.detail("old")
    assert result["average_viewers"] is None
    assert result["coverage_ratio"] is None
    assert result["legacy_metrics"]["average_viewers"] == 88
    with pytest.raises(PersistenceError,match="comparison_duration_unknown"):
        reader.compare(["s1","old"],scope="common")


def test_envelope_budget_preserves_peak_and_never_bridges_gaps():
    observations=[ViewerObservation("r",at(i*20),500 if i==70 else i%17) for i in range(200) if not 90<=i<100]
    metrics=calculate_viewers([CollectionRun("r",at(0))],observations,[],at(0),at(4000))
    method,intervals=graph_intervals(metrics,32)
    assert method == "min_max_first_last"
    assert len(intervals)<=32
    assert max(i["max"] for i in intervals) == metrics.max_viewers == 500
    assert not any(i["start"]<at(2000) and i["end"]>at(1810) for i in intervals)
    assert sum((i["end"]-i["start"]).total_seconds() for i in intervals) == metrics.covered_seconds
    fragmented=calculate_viewers([CollectionRun("r",at(0))],
                                 [ViewerObservation("r",at(i*60),i) for i in range(40)],[],at(0),at(2400))
    assert graph_intervals(fragmented,32) == ("range_required",())


def test_reader_keeps_one_revision_while_writer_changes_database(store):
    db,repo,reader=store
    with reader._read() as c:
        row=reader._stream(c,"s1")  # establishes a WAL snapshot
        before=reader._analytics(c,row,at(200))
        repo.append("s1",ViewerObservation("r",at(55),100))
        during=reader._analytics(c,row,at(200))
    after=reader.detail("s1")
    assert during==before
    assert after["max_viewers"]==100 and before["max_viewers"]==30
    assert after["data_revision"]!=before["data_revision"]


def test_comparison_page_and_untrusted_title_are_escaped(store):
    from twitchbot.container import Container
    from twitchbot.web.app import create_app
    from dataclasses import replace
    db,repo,reader=store
    StreamRepository(db).put(replace(stream("s2",40),title='</script><script>alert(1)</script>'),0)
    repo.set_end_precision("s2","confirmed")
    client=create_app(Container(history_reader=reader)).test_client()
    response=client.get("/v2/history/compare?id=s1&id=s2&scope=common")
    assert response.status_code==200
    assert "<script>alert(1)</script>" not in response.text
    assert client.get("/api/v2/streams/s1/analytics?points=1").status_code==400
    assert client.get("/api/v2/streams/s1/analytics?points=nope").status_code==400


def test_deleting_stream_cascades_analytics_state(store):
    db,repo,reader=store
    with db.connection() as c:
        c.execute("DELETE FROM streams WHERE id='s1'")
        c.commit()
        for table in ("collection_runs","viewer_observations","observation_gaps","stream_metric_state"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]==0
