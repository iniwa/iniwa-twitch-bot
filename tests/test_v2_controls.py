from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from threading import Event
import socket

import pytest

from twitchbot.adapters.persistence import SQLiteDatabase,StreamRepository,ChannelReadModelRepository
from twitchbot.adapters.persistence.community import CommunityRepository
from twitchbot.adapters.persistence.control import ControlRepository
from twitchbot.application.community import ChatMessage,Person
from twitchbot.application.control import ChannelPreset,ActionResult
from twitchbot.application.live import LiveSnapshot,StreamSnapshot,StaticLiveProvider
from twitchbot.application.live_actions import LiveActions
from twitchbot.application.persistence import StreamRecord,ChannelReadModel,PersistenceError
from twitchbot.container import Container
from twitchbot.web.app import create_app

BASE=datetime(2026,9,6,tzinfo=timezone.utc)


class FakeControl:
    available=True
    def __init__(self,result=None):
        self.calls=[]
        self.result=result or ActionResult("succeeded","marker-1",20)
    def create_marker(self,channel,description):
        self.calls.append(("marker",channel,description))
        if isinstance(self.result,Exception):raise self.result
        return self.result
    def apply_preset(self,channel,preset):
        self.calls.append(("preset",channel,preset))
        if isinstance(self.result,Exception):raise self.result
        return self.result


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,"create_connection",lambda *a,**k:pytest.fail("network used"))
    db=SQLiteDatabase(tmp_path/"controls.sqlite3");db.migrate()
    StreamRepository(db).put(StreamRecord("s1","channel","current",None,None,None,(),BASE,None,None,"bot","partial",None,None,None,None,{},None),0)
    clock=[BASE+timedelta(seconds=20)]
    live=StaticLiveProvider(LiveSnapshot(stream=StreamSnapshot(state="live",id="s1",title="current",observed_at=clock[0],started_at=BASE),generated_at=clock[0]))
    repo=ControlRepository(db,"channel",clock=lambda:clock[0])
    ChannelReadModelRepository(db).put(ChannelReadModel("channel","current","game","Current game",("tag",),None,clock[0],"fake"),0)
    adapter=FakeControl()
    actions=LiveActions(repo,live,adapter=adapter,runtime_allowed=lambda:True)
    return db,repo,actions,clock


def preset():
    return ChannelPreset("p1","冒険用","new title",None,None,(),("告知用",))


def test_unresolved_category_never_previews_as_current_category(store):
    _, repo, actions, _ = store
    repo.save_preset(replace(preset(), game_name='Needs resolution'), 0)
    with pytest.raises(PersistenceError, match='preset_category_unresolved'):
        repo.preview_preset('p1')
    assert actions.adapter.calls == []


def test_save_presets_and_notes_do_not_dispatch_and_detect_revision_conflicts(store):
    db,repo,actions,clock=store
    assert repo.save_preset(preset(),0)["revision"]==1
    with pytest.raises(PersistenceError,match="revision_conflict"):repo.save_preset(preset(),0)
    result=actions.note("s1","ローカルメモ","n1")
    assert result["operation"] is None
    assert actions.adapter.calls==[]
    assert repo.notes("s1")["items"][0]["body"]=="ローカルメモ"


def test_preset_display_sort_uses_allowed_columns_without_changing_saved_data(store):
    db,repo,actions,clock=store
    repo.save_preset(preset(),0)
    clock[0]+=timedelta(seconds=1)
    repo.save_preset(replace(preset(),id="p2",name="Ａセット"),0)
    assert [item["id"] for item in repo.presets()["items"]] == ["p2","p1"]
    assert [item["id"] for item in repo.presets(sort="updated_at",order="desc")["items"]] == ["p2","p1"]
    assert [item["id"] for item in repo.presets(sort="updated_at",order="asc")["items"]] == ["p1","p2"]
    with pytest.raises(PersistenceError,match="invalid_sort"):
        repo.presets(sort="updated_at DESC")
    with pytest.raises(PersistenceError,match="invalid_order"):
        repo.presets(order="sideways")


def test_marker_success_is_not_repeated_for_duplicate_request(store):
    db,repo,actions,clock=store
    first=actions.note("s1","見どころ","m1",marker=True)
    second=actions.note("s1","見どころ","m1",marker=True)
    assert first==second
    assert first["operation"]["remote_id"]=="marker-1"
    assert first["operation"]["position_seconds"]==20
    assert len(actions.adapter.calls)==1
    with pytest.raises(PersistenceError,match="record_conflict"):
        actions.note("s1","別の内容","m1",marker=True)


@pytest.mark.parametrize("result,state",[(TimeoutError("private provider message"),"unknown"),(ActionResult("failed"),"failed"),(ActionResult("partial"),"partial"),(ActionResult("succeeded"),"unknown")])
def test_marker_failure_and_uncertainty_always_keep_local_note(store,result,state):
    db,repo,actions,clock=store
    actions.adapter=FakeControl(result)
    saved=actions.note("s1","大切なメモ","m1",marker=True)
    assert saved["operation"]["state"]==state
    assert "private" not in str(saved)
    assert repo.notes("s1")["total"]==1
    actions.note("s1","大切なメモ","m1",marker=True)
    assert len(actions.adapter.calls)==1


def test_stale_or_offline_stream_keeps_note_and_rejects_marker_dispatch(store):
    db,repo,actions,clock=store
    clock[0]+=timedelta(seconds=61)
    result=actions.note("s1","遅れた記録","stale",marker=True)
    assert result["operation"]["result_code"]=="live_state_unconfirmed"
    assert actions.adapter.calls==[]
    unavailable=LiveActions(repo,actions.live_provider)
    clock[0]-=timedelta(seconds=61)
    result=unavailable.note("s1","未接続","unavailable",marker=True)
    assert result["operation"]["state"]=="unavailable"


def test_preset_preview_preserves_category_and_explicitly_removes_tags(store):
    db,repo,actions,clock=store
    repo.save_preset(preset(),0)
    preview=repo.preview_preset("p1")
    assert preview["before"]["title"]=="current"
    assert preview["after"]["title"]=="new title"
    assert preview["after"]["game_id"]=="game"
    assert preview["after"]["tags"]==[]
    first=actions.apply_preset(preview["id"],"a1")
    second=actions.apply_preset(preview["id"],"a2")
    assert first==second and len(actions.adapter.calls)==1
    sent=actions.adapter.calls[0][2]
    assert sent.game_id=="game" and sent.tags==()
    assert not hasattr(sent,"social_tags") and not hasattr(sent,"name")


def test_preset_preview_conflicts_stale_snapshot_and_no_change(store):
    db,repo,actions,clock=store
    repo.save_preset(preset(),0)
    preview=repo.preview_preset("p1")
    repo.save_preset(replace(preset(),title="edited"),1)
    with pytest.raises(PersistenceError,match="preview_changed"):actions.apply_preset(preview["id"],"a1")
    preview=repo.preview_preset("p1")
    clock[0]+=timedelta(seconds=61)
    with pytest.raises(PersistenceError,match="channel_snapshot_stale"):actions.apply_preset(preview["id"],"a2")
    clock[0]-=timedelta(seconds=61)
    repo.save_preset(replace(preset(),title="current",tags=("tag",)),2)
    preview=repo.preview_preset("p1")
    assert actions.apply_preset(preview["id"],"no-change")["state"]=="no_change"
    assert actions.adapter.calls==[]


def test_unknown_preset_blocks_new_dispatch_until_reconciled(store):
    db,repo,actions,clock=store
    repo.save_preset(preset(),0)
    preview=repo.preview_preset("p1")
    actions.adapter=FakeControl(TimeoutError())
    assert actions.apply_preset(preview["id"],"a1")["state"]=="unknown"
    next_preview=repo.preview_preset("p1")
    with pytest.raises(PersistenceError,match="operation_unresolved"):
        actions.apply_preset(next_preview["id"],"a2")
    assert len(actions.adapter.calls)==1


def test_crash_recovery_does_not_send_saved_pending_intents(store):
    db,repo,actions,clock=store
    repo.create_note("interrupted","s1","メモ",marker=True)
    assert repo.recover_interrupted()==1
    assert repo.recover_interrupted()==0
    result=actions.note("s1","メモ","interrupted",marker=True)
    assert result["operation"]["state"]=="unknown"
    assert actions.adapter.calls==[]


def test_full_stop_blocks_outbound_actions_but_keeps_local_note(store):
    db,repo,actions,clock=store
    actions.runtime_allowed=lambda:False
    result=actions.note("s1","停止中のメモ","stopped",marker=True)
    assert result["operation"]["result_code"]=="runtime_stopped"
    assert repo.notes("s1")["total"]==1 and actions.adapter.calls==[]


def test_preset_changed_after_acceptance_is_not_dispatched(store):
    db,repo,actions,clock=store
    repo.save_preset(preset(),0)
    preview=repo.preview_preset("p1")
    operation,definition,created=repo.accept_preset(preview["id"],"accepted")
    repo.save_preset(replace(preset(),title="later edit"),1)
    assert not repo.claim_preset(operation["id"])
    assert repo.operation(operation["id"])["result_code"]=="preview_changed"


def test_concurrent_duplicate_dispatch_is_claimed_only_once(store):
    db,repo,actions,clock=store
    entered,release=Event(),Event()
    def block(channel,description):
        entered.set()
        assert release.wait(5)
        return ActionResult("succeeded","remote",20)
    actions.adapter.create_marker=block
    with ThreadPoolExecutor(max_workers=2) as executor:
        first=executor.submit(actions.note,"s1","同じ操作","same",marker=True)
        assert entered.wait(5)
        second=executor.submit(actions.note,"s1","同じ操作","same",marker=True).result(timeout=5)
        assert second["operation"]["state"]=="dispatching"
        release.set()
        assert first.result(timeout=5)["operation"]["state"]=="succeeded"


def test_person_notes_use_separate_revisions_from_recording(store):
    db,repo,actions,clock=store
    community=CommunityRepository(db,"channel",clock=lambda:clock[0])
    person=Person("u1")
    community.record_chat(ChatMessage("chat",person,"s1",BASE,clock[0],"body"))
    assert repo.save_person_note("u1","覚えておくこと",0)["revision"]==1
    community.record_chat(ChatMessage("chat2",person,"s1",BASE,clock[0],"body"))
    assert repo.save_person_note("u1","追記",1)["revision"]==2
    with pytest.raises(PersistenceError,match="revision_conflict"):repo.save_person_note("u1","競合",1)
    assert repo.person_note("u1")["body"]=="追記"


def test_new_control_pages_and_mutations_keep_pilot_readonly(store,monkeypatch):
    db,repo,actions,clock=store
    container=Container(live_provider=actions.live_provider,live_actions=actions,community=repo.records)
    client=create_app(container).test_client()
    repo.save_preset(preset(),0)
    for path in ("/v2/control","/v2/presets","/api/v2/control","/api/v2/presets"):
        response=client.get(path)
        assert response.status_code==200
        assert response.headers["Cache-Control"]=="no-store"
        if path == "/v2/control":
            assert '<main data-page="live">' in response.text and '/v2-static/v2/app.js' in response.text
        if path == "/v2/presets":
            assert '<main data-page="presets">' in response.text and '/v2-static/v2/app.js' in response.text
    sorted_response=client.get("/api/v2/presets?sort=updated_at&order=desc")
    assert sorted_response.status_code==200 and sorted_response.json["sort"]=="updated_at"
    assert client.get("/api/v2/presets?sort=name%20DESC").status_code==400
    assert client.get("/api/v2/presets?sort=name&sort=updated_at").status_code==400
    assert actions.adapter.calls==[]
    assert client.post("/api/v2/streams/s1/markers",json={}).status_code==403
    response=client.post("/api/v2/streams/s1/markers",json={"request_id":"web","body":"メモ","marker":False},headers={"Origin":"http://localhost"})
    assert response.status_code==200
    assert actions.adapter.calls==[]
    monkeypatch.setattr(db,"connect",lambda:pytest.fail("GET wrote to DB"))
    assert client.get("/api/v2/control").status_code==200
    assert "読み取り専用" in client.get("/v2/live").text


def test_monitor_provides_server_clock_without_refreshing_snapshot(store):
    db,repo,actions,clock=store
    client=create_app(Container(live_provider=actions.live_provider)).test_client()
    before=datetime.now(timezone.utc)
    data=client.get("/api/v2/control").get_json()
    after=datetime.now(timezone.utc)
    assert before <= datetime.fromisoformat(data["server_time"]) <= after
    assert data["snapshot"] == actions.live_provider.snapshot().as_dict()
    assert actions.adapter.calls == []
