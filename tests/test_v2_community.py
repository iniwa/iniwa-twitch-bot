from dataclasses import replace
from datetime import datetime, timedelta, timezone
import socket

import pytest

from twitchbot.adapters.persistence import SQLiteDatabase, StreamRepository
from twitchbot.adapters.persistence.community import CommunityRepository
from twitchbot.adapters.persistence.migrations import MIGRATIONS
from twitchbot.application.community import ChannelEvent, ChatMessage, Follower, Person
from twitchbot.application.persistence import PersistenceError, StreamRecord
from twitchbot.container import Container
from twitchbot.web.app import create_app

BASE = datetime(2026,9,6,tzinfo=timezone.utc)
PERSON = Person("u1","user","架空のユーザー")


def at(seconds):
    return BASE + timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(socket,"create_connection",lambda *a,**k: pytest.fail("network used"))
    db = SQLiteDatabase(tmp_path / "community.sqlite3")
    db.migrate()
    for sid,channel,start,end in (("s1","channel",0,100),("s2","channel",200,300),("other","other",0,100)):
        StreamRepository(db).put(StreamRecord(sid,channel,"架空の配信",None,None,None,(),at(start),at(end),end-start,"bot","partial",None,None,None,None,{},None),0)
    now=[at(400)]
    repo=CommunityRepository(db,"channel",clock=lambda:now[0])
    return db,repo,now


def event(key="e1", seconds=10, **kwargs):
    return ChannelEvent(key,"follow",at(seconds),at(350),PERSON,**kwargs)


def chat(key="m1", seconds=10, body="架空の本文", **kwargs):
    return ChatMessage(key,kwargs.get("person",PERSON),kwargs.get("stream_id","s1"),at(seconds),at(350),body)


def complete_sync(repo,key,followers):
    repo.start_sync(key,len(followers))
    repo.append_sync_page(key,followers,total=len(followers))
    return repo.finish_sync(key,success=True)


def test_forward_upgrade_keeps_prior_checksums_and_rows(tmp_path):
    old=SQLiteDatabase(tmp_path / "upgrade.sqlite3",migrations=MIGRATIONS[:3])
    old.migrate()
    with old.connection() as c:
        before=[tuple(r) for r in c.execute("SELECT * FROM schema_migrations")]
    db=SQLiteDatabase(old.path);db.migrate();db.migrate()
    with db.connection() as c:
        assert [tuple(r) for r in c.execute("SELECT * FROM schema_migrations WHERE version<=3")] == before
        assert c.execute("SELECT COUNT(*) FROM channel_events").fetchone()[0] == 0


def test_delayed_offline_and_unknown_events_do_not_use_receipt_stream(store):
    db,repo,now=store
    repo.record_event(event(stream_id="s1",attribution="stream"))
    repo.record_event(event("off",150,attribution="offline"))
    repo.record_event(ChannelEvent("unknown","raid",None,at(250),amount=7))
    assert repo.events(stream_id="s1")["total"] == 1
    assert repo.events(stream_id="s2")["total"] == 0
    assert repo.events(attribution="offline")["items"][0]["id"] == "off"
    assert repo.events(attribution="unknown")["items"][0]["occurred_at"] is None
    with pytest.raises(PersistenceError,match="event_outside_stream"):
        repo.record_event(event("wrong",10,stream_id="s2",attribution="stream"))
    with pytest.raises(PersistenceError):
        repo.record_event(event("foreign",10,stream_id="other",attribution="stream"))


def test_event_and_follow_semantic_deduplication_are_atomic(store):
    db,repo,now=store
    assert repo.record_event(event())
    revision=repo.events()["data_revision"]
    assert not repo.record_event(replace(event(),received_at=at(500)))
    assert not repo.record_event(event("other-delivery"))
    assert repo.events()["data_revision"] == revision
    with pytest.raises(PersistenceError,match="record_conflict"):
        repo.record_event(event(seconds=11))
    assert complete_sync(repo,"sync",[Follower(PERSON,at(10))]) == "complete"
    assert repo.followers()["total"] == 1
    assert repo.events()["total"] == 1


def test_partial_failed_and_empty_incomplete_sync_never_unfollow(store):
    db,repo,now=store
    repo.record_event(event())
    repo.start_sync("partial",2)
    repo.append_sync_page("partial",[Follower(PERSON,at(10))],next_cursor="p2",total=2)
    with pytest.raises(PersistenceError,match="incomplete_sync"):
        repo.finish_sync("partial",success=True)
    assert repo.finish_sync("partial",success=False) == "failed"
    repo.start_sync("not-started",0)
    with pytest.raises(PersistenceError):repo.finish_sync("not-started",success=True)
    assert repo.people()["items"][0]["follow_status"] == "following"
    assert repo.followers()["total"] == 1


def test_complete_sync_unfollow_and_refollow_keep_the_full_history(store):
    db,repo,now=store
    repo.record_event(event())
    assert complete_sync(repo,"empty",[]) == "complete"
    assert repo.people()["items"][0]["follow_status"] == "not_following"
    removal=repo.followers()["items"][0]
    assert removal["kind"] == "unfollow_detected" and removal["occurred_at"] is None
    repo.record_event(ChannelEvent("refollow","follow",at(450),at(460),PERSON,attribution="offline"))
    assert repo.people()["items"][0]["follow_status"] == "following"
    assert sorted(r["kind"] for r in repo.followers()["items"]) == ["follow","refollow","unfollow_detected"]
    assert repo.finish_sync("empty",success=True) == "complete"
    assert repo.followers()["total"] == 3


def test_sync_changed_during_pagination_is_superseded(store):
    db,repo,now=store
    repo.record_event(event())
    repo.start_sync("stale",0)
    repo.append_sync_page("stale",[],total=0)
    repo.record_event(replace(event("new",20),person=Person("u2")))
    assert repo.finish_sync("stale",success=True) == "superseded"
    assert {p["follow_status"] for p in repo.people()["items"]} == {"following"}
    assert repo.followers()["total"] == 2


def test_sync_duplicate_and_cycles_roll_back_the_whole_page(store):
    db,repo,now=store
    repo.start_sync("pages",2)
    repo.append_sync_page("pages",[Follower(PERSON,at(10))],next_cursor="p2",total=2)
    with pytest.raises(PersistenceError,match="duplicate_sync_member"):
        repo.append_sync_page("pages",[Follower(Person("u2"),at(20)),Follower(PERSON,at(10))],cursor="p2",total=2)
    with pytest.raises(PersistenceError,match="cursor_cycle"):
        repo.append_sync_page("pages",[],cursor="p2",next_cursor="p2",total=2)
    with pytest.raises(PersistenceError):
        repo.append_sync_page("pages",[],cursor="p2",total=3)
    repo.append_sync_page("pages",[Follower(Person("u2"),at(20))],cursor="p2",total=2)
    assert repo.finish_sync("pages",success=True) == "complete"
    assert repo.people()["total"] == 2


def test_late_old_follow_does_not_reverse_detected_unfollow(store):
    db,repo,now=store
    repo.record_event(event(seconds=30))
    complete_sync(repo,"empty",[])
    repo.record_event(event("old",10))
    assert repo.people()["items"][0]["follow_status"] == "not_following"
    assert [r["kind"] for r in sorted(repo.followers()["items"],key=lambda r:r["occurred_at"] or "z")][:2] == ["follow","refollow"]


def test_chat_identity_participation_rename_and_offline_exclusion(store):
    db,repo,now=store
    assert repo.record_chat(chat())
    assert not repo.record_chat(chat())
    assert repo.record_chat(chat("earlier",5,person=Person("u1","new_login","新しい名前")))
    assert not repo.record_chat(chat("offline",150,stream_id=None))
    participation=repo.person("u1")
    assert participation["total"] == 1
    assert participation["items"][0]["comment_count"] == 2
    assert participation["items"][0]["first_seen_at"].startswith("2026-09-06T00:00:05")
    assert participation["person"]["display_name"] == "新しい名前"
    assert repo.chats()["total"] == 2
    assert repo.people(stream_id="s2")["total"] == 0


def test_body_preview_detects_new_matching_records_but_not_unrelated_events(store):
    db,repo,now=store
    repo.record_chat(chat())
    preview=repo.preview_body_deletion(at(0),at(100))
    repo.record_chat(chat("new",20))
    with pytest.raises(PersistenceError,match="preview_changed"):
        repo.delete_chat_bodies(preview["id"])
    assert all(r["body"] is not None for r in repo.chats()["items"])
    preview=repo.preview_body_deletion(at(0),at(100))
    repo.record_event(event())
    result=repo.delete_chat_bodies(preview["id"])
    assert result["message_count"] == 2
    assert all(r["body"] is None for r in repo.chats()["items"])
    assert repo.person("u1")["items"][0]["comment_count"] == 2
    assert repo.events()["total"] == 1
    assert not repo.record_chat(chat())
    assert repo.delete_chat_bodies(preview["id"]) == result
    assert all(r["body"] is None for r in repo.chats()["items"])
    with db.connection() as c:
        # Event and audit models contain no second copy of the deleted body.
        for table in ("channel_events","follow_history","chat_body_deletions","viewer_streams"):
            assert "架空の本文" not in repr([tuple(r) for r in c.execute(f"SELECT * FROM {table}")])


def test_body_deletion_expiration_and_channel_isolation(store):
    db,repo,now=store
    repo.record_chat(chat())
    preview=repo.preview_body_deletion(at(0),at(100))
    other=CommunityRepository(db,"other",clock=lambda:now[0])
    with pytest.raises(PersistenceError,match="preview_not_found"):
        other.delete_chat_bodies(preview["id"])
    assert other.people()["total"] == 0 and other.chats()["total"] == 0
    now[0]+=timedelta(hours=1)
    with pytest.raises(PersistenceError,match="preview_expired"):
        repo.delete_chat_bodies(preview["id"])


def test_query_paging_and_sqlite_snapshot(store):
    db,repo,now=store
    repo.record_event(event())
    repo.record_event(event("e2",20))
    first=repo.events(limit=1)
    second=repo.events(limit=1,before=first["next_cursor"])
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["total"] == second["total"] == 2
    assert second["next_cursor"] is None
    with repo.transaction() as c:
        before=repo._revision(c)
        repo.record_event(event("e3",30))
        assert repo._revision(c) == before
    assert repo.events()["data_revision"] != before[0]


def test_sort_filters_and_revision_bound_cursor(store):
    db,repo,now=store
    repo.record_event(event("late",30))
    repo.record_event(replace(event("early",10),person=Person("u2","zeta","Ｚｅｔａ")))
    page=repo.followers(sort="name",order="asc",limit=1)
    assert page["items"][0]["user_id"] == "u2"
    assert page["sort"] == "name" and page["order"] == "asc"
    assert page["next_cursor"]["filters"] == {"user_id":"","kind":""}
    with pytest.raises(PersistenceError,match="invalid_cursor"):
        repo.followers(sort="name",order="desc",limit=1,before=page["next_cursor"])
    repo.record_event(replace(event("new",20),person=Person("u3")))
    with pytest.raises(PersistenceError,match="list_changed"):
        repo.followers(sort="name",order="asc",limit=1,before=page["next_cursor"])
    assert repo.followers(kind="follow")["total"] == 3
    with pytest.raises(PersistenceError,match="invalid_sort"):
        repo.followers(sort="detected_at DESC; DROP TABLE viewers")


def test_follower_status_keeps_unknown_and_nulls_last_in_both_directions(store):
    db,repo,now=store
    repo.record_event(event())
    repo.record_chat(chat("unknown",20,person=Person("u2","viewer2","未確認の人")))
    assert complete_sync(repo,"empty",[]) == "complete"
    statuses=repo.follower_status(sort="followed_at",order="asc")
    assert [item["status"] for item in statuses["items"]] == ["not_following","unknown"]
    statuses=repo.follower_status(sort="followed_at",order="desc")
    assert [item["status"] for item in statuses["items"]] == ["not_following","unknown"]
    assert repo.follower_status(status="not_following")["total"] == 1
    assert repo.follower_status(status="unknown")["items"][0]["user_id"] == "u2"
    assert statuses["last_successful_sync_at"] is not None
    for order in ("asc","desc"):
        history=repo.followers(sort="occurred_at",order=order)["items"]
        assert history[-1]["kind"] == "unfollow_detected"


def test_last_successful_sync_survives_a_later_failed_attempt(store):
    db,repo,now=store
    repo.record_event(event())
    complete_sync(repo,"complete",[Follower(PERSON,at(10))])
    successful=repo.followers()["last_successful_sync_at"]
    now[0]+=timedelta(minutes=1)
    repo.start_sync("failed",1)
    assert repo.finish_sync("failed",success=False) == "failed"
    result=repo.follower_status()
    assert result["latest_sync"]["state"] == "failed"
    assert result["last_successful_sync_at"] == successful


def test_legacy_cursor_is_only_accepted_for_the_legacy_default_order(store):
    db,repo,now=store
    repo.record_event(event("one",10))
    repo.record_event(event("two",20))
    first=repo.events(limit=1)
    row=first["items"][0]
    legacy=[row["sort_at"],row["id"]]
    assert repo.events(limit=1,before=legacy)["items"][0]["id"] != row["id"]
    with pytest.raises(PersistenceError,match="invalid_cursor"):
        repo.events(sort="received_at",before=legacy)


def test_community_pages_api_and_write_origin(store,monkeypatch):
    db,repo,now=store
    repo.record_chat(chat(body="<script>alert('sentinel')</script>"))
    repo.record_event(event())
    app=create_app(Container(community=repo));app.testing=True
    client=app.test_client()
    for path in ("/v2/community","/v2/community/events","/v2/community/followers","/v2/community/followers/current","/v2/community/chat","/v2/community/people/u1","/api/v2/events","/api/v2/follow-history","/api/v2/follower-status","/api/v2/viewers/u1/history","/api/v2/chat-messages"):
        response=client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        if path.startswith("/v2/"):
            assert "<script>alert('sentinel')</script>" not in response.text
            assert response.text.count('class="app-nav-link"') == 6
            assert '配信セット' in response.text
            assert '/v2-static/v2/app.js' in response.text
            assert '<main data-page="community-' in response.text
    for token in ("oops","3","{}","[1,2]"):
        assert client.get("/api/v2/events",query_string={"cursor":token}).status_code == 400
    assert client.get("/api/v2/follow-history",query_string={"kind":"unfollow_detected"}).status_code == 200
    assert client.get("/api/v2/follower-status",query_string={"status":"unknown"}).status_code == 200
    assert client.get("/api/v2/follow-history",query_string={"sort":"detected_at desc"}).status_code == 400
    assert client.get("/api/v2/follow-history?sort=name&sort=detected_at").status_code == 400
    assert client.get("/api/v2/follower-status?status=unknown&status=following").status_code == 400
    assert client.get("/api/v2/events?limit=10&limit=20").status_code == 400
    body={"start":at(0).isoformat(),"end":at(100).isoformat()}
    assert client.post("/api/v2/chat-body-deletion-previews",json=body).status_code == 403
    assert client.post("/api/v2/chat-body-deletion-previews",json=body,headers={"Origin":"https://outside.invalid"}).status_code == 403
    headers={"Origin":"http://localhost"}
    preview=client.post("/api/v2/chat-body-deletion-previews",json=body,headers=headers).get_json()
    response=client.post("/api/v2/chat-body-deletions",json={"preview_id":preview["id"]},headers=headers)
    assert response.status_code == 200 and response.json["message_count"] == 1
    monkeypatch.setattr(db,"connect",lambda:pytest.fail("GET opened writable connection"))
    assert client.get("/api/v2/events").status_code == 200
    assert client.get("/api/v2/viewers/missing/history").status_code == 404


def test_default_app_and_missing_store_never_create_database(tmp_path):
    client=create_app().test_client()
    assert client.get("/api/v2/events").status_code == 503
    path=tmp_path / "absent.sqlite3"
    repo=CommunityRepository(SQLiteDatabase(path),"channel")
    with pytest.raises(PersistenceError):repo.events()
    assert not path.exists()


@pytest.mark.parametrize("make",[
    lambda:ChannelEvent("e","follow",None,at(10),PERSON),
    lambda:ChannelEvent("e","raid",None,at(10),attribution="offline"),
    lambda:ChannelEvent("e","raid",at(11),at(10)),
    lambda:ChannelEvent("e","raid",at(0),at(10),amount=True),
    lambda:ChatMessage("m",PERSON,"s1",at(10),at(0),"text"),
])
def test_invalid_normalized_records(make):
    with pytest.raises(PersistenceError):make()
