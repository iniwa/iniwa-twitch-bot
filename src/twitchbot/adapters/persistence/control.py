"""Durable local notes/presets and an at-most-once dispatch ledger."""

from datetime import timedelta
from dataclasses import replace
import json
from uuid import uuid4

from ...application.analytics import identifier
from ...application.community import text_field
from ...application.control import ChannelPreset
from ...application.persistence import PersistenceError, RevisionConflictError
from .community import CommunityRepository
from .sqlite import from_rfc3339, to_rfc3339, utc_now


class ControlRepository:
    def __init__(self, database, channel_id, *, clock=utc_now):
        self.records = CommunityRepository(database, channel_id, clock=clock)
        self.channel_id = self.records.channel_id
        self.clock = clock

    @staticmethod
    def _expected(value):
        if type(value) is not int or value < 0:
            raise PersistenceError("invalid_revision", "control")

    @staticmethod
    def _preset(row):
        return ChannelPreset(row["id"], row["name"], row["title"], row["game_id"], row["game_name"], tuple(json.loads(row["tags_json"])), tuple(json.loads(row["social_tags_json"])))

    @staticmethod
    def _preset_dict(row):
        result = dict(row)
        result["tags"] = json.loads(result.pop("tags_json"))
        result["social_tags"] = json.loads(result.pop("social_tags_json"))
        return result

    def save_preset(self, preset: ChannelPreset, expected_revision):
        self._expected(expected_revision)
        with self.records.transaction(write=True) as c:
            old = c.execute("SELECT revision FROM channel_presets WHERE channel_id=? AND id=?", (self.channel_id, preset.id)).fetchone()
            if (old[0] if old else 0) != expected_revision:
                raise RevisionConflictError("preset")
            now = to_rfc3339(self.clock())
            c.execute("INSERT INTO channel_presets VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,id) DO UPDATE SET name=excluded.name,title=excluded.title,game_id=excluded.game_id,game_name=excluded.game_name,tags_json=excluded.tags_json,social_tags_json=excluded.social_tags_json,revision=excluded.revision,updated_at=excluded.updated_at", (self.channel_id, preset.id, preset.name, preset.title, preset.game_id, preset.game_name, json.dumps(preset.tags), json.dumps(preset.social_tags), expected_revision+1, now, now))
            self.records._bump(c)
            return self._preset_dict(c.execute("SELECT * FROM channel_presets WHERE channel_id=? AND id=?", (self.channel_id, preset.id)).fetchone())

    def presets(self, *, sort="name", order="asc"):
        sort_map = {"name": "sort_name(name)", "updated_at": "updated_at"}
        if sort not in sort_map:
            raise PersistenceError("invalid_sort", "control")
        if order not in ("asc", "desc"):
            raise PersistenceError("invalid_order", "control")
        with self.records.transaction() as c:
            direction = order.upper()
            rows = c.execute(f"SELECT * FROM channel_presets WHERE channel_id=? ORDER BY {sort_map[sort]} {direction},id {direction} LIMIT 201", (self.channel_id,)).fetchall()
            if len(rows)>200:
                raise PersistenceError("preset_limit_exceeded", "control")
            return {"items": [self._preset_dict(row) for row in rows], "sort": sort, "order": order}

    def person_note(self, user_id):
        identifier(user_id)
        with self.records.transaction() as c:
            row = c.execute("SELECT user_id,body,revision,updated_at FROM person_notes WHERE channel_id=? AND user_id=?", (self.channel_id, user_id)).fetchone()
            return dict(row) if row else {"user_id": user_id, "body": "", "revision": 0, "updated_at": None}

    def save_person_note(self, user_id, body, expected_revision):
        identifier(user_id)
        text_field(body, 10_000)
        self._expected(expected_revision)
        with self.records.transaction(write=True) as c:
            if c.execute("SELECT 1 FROM community_people WHERE channel_id=? AND user_id=?", (self.channel_id, user_id)).fetchone() is None:
                raise PersistenceError("person_not_found", "control")
            row = c.execute("SELECT revision FROM person_notes WHERE channel_id=? AND user_id=?", (self.channel_id, user_id)).fetchone()
            if (row[0] if row else 0) != expected_revision:
                raise RevisionConflictError("person_note")
            now = to_rfc3339(self.clock())
            c.execute("INSERT INTO person_notes VALUES (?,?,?,?,?) ON CONFLICT(channel_id,user_id) DO UPDATE SET body=excluded.body,revision=excluded.revision,updated_at=excluded.updated_at", (self.channel_id, user_id, body, expected_revision+1, now))
            self.records._bump(c)
            return {"user_id": user_id, "body": body, "revision": expected_revision+1, "updated_at": now}

    def create_note(self, request_id, stream_id, body, *, marker):
        identifier(request_id)
        identifier(stream_id)
        text_field(body, 500)
        if type(marker) is not bool:
            raise PersistenceError("invalid_marker_request", "control")
        now = to_rfc3339(self.clock())
        with self.records.transaction(write=True) as c:
            old = c.execute("SELECT * FROM stream_notes WHERE channel_id=? AND id=?", (self.channel_id, request_id)).fetchone()
            if old:
                if (old["stream_id"], old["body"], old["marker_requested"]) != (stream_id, body, int(marker)):
                    raise PersistenceError("record_conflict", "control")
                return dict(old), False
            if c.execute("SELECT 1 FROM streams WHERE channel_id=? AND id=?", (self.channel_id, stream_id)).fetchone() is None:
                raise PersistenceError("stream_not_found", "control")
            c.execute("INSERT INTO stream_notes VALUES (?,?,?,?,?,?,1,?)", (self.channel_id, request_id, stream_id, now, body, int(marker), now))
            if marker:
                c.execute("INSERT INTO control_operations VALUES (?,?,'marker',?,NULL,'pending','accepted',NULL,NULL,?,NULL)", (self.channel_id, request_id, request_id, now))
            self.records._bump(c)
            return dict(c.execute("SELECT * FROM stream_notes WHERE channel_id=? AND id=?", (self.channel_id, request_id)).fetchone()), True

    def notes(self, stream_id, *, limit=50, before=None):
        identifier(stream_id)
        with self.records.transaction() as c:
            query = "SELECT n.*,o.state AS marker_state,o.remote_id,o.position_seconds FROM stream_notes n LEFT JOIN control_operations o ON o.channel_id=n.channel_id AND o.id=n.id WHERE n.channel_id=? AND n.stream_id=?"
            return self.records._page(c, query, [self.channel_id,stream_id], limit=limit, before=before, time_key="occurred_at", id_key="id")

    def preview_preset(self, preset_id):
        identifier(preset_id)
        with self.records.transaction(write=True) as c:
            preset = c.execute("SELECT * FROM channel_presets WHERE channel_id=? AND id=?", (self.channel_id, preset_id)).fetchone()
            current = c.execute("SELECT * FROM channel_read_model WHERE channel_id=?", (self.channel_id,)).fetchone()
            if preset is None:
                raise PersistenceError("preset_not_found", "control")
            if preset['game_name'] and preset['game_id'] is None:
                raise PersistenceError('preset_category_unresolved', 'control')
            if current is None:
                raise PersistenceError("channel_snapshot_unavailable", "control")
            key = uuid4().hex
            c.execute("INSERT INTO preset_previews VALUES (?,?,?,?,?,?)", (self.channel_id, key, preset_id, preset["revision"], current["revision"], to_rfc3339(self.clock()+timedelta(minutes=5))))
            after = self._preset_dict(preset)
            if after["game_id"] is None:
                after.update(game_id=current["game_id"], game_name=current["game_name"])
            return {"id": key, "before": {"title": current["title"], "game_id": current["game_id"], "game_name": current["game_name"], "tags": json.loads(current["tags_json"]), "observed_at": current["observed_at"]}, "after": after}

    def accept_preset(self, preview_id, request_id):
        identifier(preview_id)
        identifier(request_id)
        with self.records.transaction(write=True) as c:
            existing = c.execute("SELECT * FROM control_operations WHERE channel_id=? AND (id=? OR preview_id=?)", (self.channel_id, request_id, preview_id)).fetchall()
            if existing:
                if len(existing) != 1 or existing[0]["kind"] != "preset" or existing[0]["preview_id"] != preview_id:
                    raise PersistenceError("record_conflict", "control")
                return dict(existing[0]), None, False
            preview = c.execute("SELECT * FROM preset_previews WHERE channel_id=? AND id=?", (self.channel_id, preview_id)).fetchone()
            if preview is None:
                raise PersistenceError("preview_not_found", "control")
            if to_rfc3339(self.clock()) >= preview["expires_at"]:
                raise PersistenceError("preview_expired", "control")
            preset = c.execute("SELECT * FROM channel_presets WHERE channel_id=? AND id=?", (self.channel_id, preview["preset_id"])).fetchone()
            current = c.execute("SELECT * FROM channel_read_model WHERE channel_id=?", (self.channel_id,)).fetchone()
            if current is None or preset["revision"] != preview["preset_revision"] or current["revision"] != preview["channel_revision"]:
                raise PersistenceError("preview_changed", "control")
            age = (self.clock() - from_rfc3339(current["observed_at"])).total_seconds()
            if not 0 <= age <= 60:
                raise PersistenceError("channel_snapshot_stale", "control")
            if c.execute("SELECT 1 FROM control_operations WHERE channel_id=? AND kind='preset' AND state IN ('pending','dispatching','unknown')", (self.channel_id,)).fetchone():
                raise PersistenceError("operation_unresolved", "control")
            desired = self._preset(preset)
            if desired.game_id is None:
                desired = replace(desired, game_id=current["game_id"], game_name=current["game_name"])
            same = (desired.title,desired.game_id,desired.tags) == (current["title"],current["game_id"],tuple(json.loads(current["tags_json"])))
            state = "no_change" if same else "pending"
            now = to_rfc3339(self.clock())
            c.execute("INSERT INTO control_operations VALUES (?,?,'preset',?,?,?, ?,NULL,NULL,?,?)", (self.channel_id,request_id,preset["id"],preview_id,state,state,now,now if same else None))
            return dict(c.execute("SELECT * FROM control_operations WHERE channel_id=? AND id=?", (self.channel_id,request_id)).fetchone()), desired, True

    def operation(self, request_id):
        identifier(request_id)
        with self.records.transaction() as c:
            row = c.execute("SELECT * FROM control_operations WHERE channel_id=? AND id=?", (self.channel_id,request_id)).fetchone()
            if row is None:
                raise PersistenceError("operation_not_found", "control")
            return dict(row)

    def claim_preset(self, request_id):
        """Recheck the saved definition and channel snapshot immediately before send."""
        with self.records.transaction(write=True) as c:
            operation=c.execute("SELECT * FROM control_operations WHERE channel_id=? AND id=?",(self.channel_id,request_id)).fetchone()
            if operation is None or operation["state"]!="pending":
                return False
            preview=c.execute("SELECT * FROM preset_previews WHERE channel_id=? AND id=?",(self.channel_id,operation["preview_id"])).fetchone()
            preset=c.execute("SELECT revision FROM channel_presets WHERE channel_id=? AND id=?",(self.channel_id,operation["target_id"])).fetchone()
            current=c.execute("SELECT revision,observed_at FROM channel_read_model WHERE channel_id=?",(self.channel_id,)).fetchone()
            valid=preview is not None and preset is not None and current is not None and preset[0]==preview["preset_revision"] and current[0]==preview["channel_revision"] and 0<=(self.clock()-from_rfc3339(current[1])).total_seconds()<=60
            c.execute("UPDATE control_operations SET state=?,result_code=?,finished_at=? WHERE channel_id=? AND id=?",("dispatching" if valid else "failed","dispatching" if valid else "preview_changed",None if valid else to_rfc3339(self.clock()),self.channel_id,request_id))
            self.records._bump(c)
            return valid

    def transition(self, request_id, expected, state, *, code, remote_id=None, position=None):
        with self.records.transaction(write=True) as c:
            changed = c.execute("UPDATE control_operations SET state=?,result_code=?,remote_id=?,position_seconds=?,finished_at=? WHERE channel_id=? AND id=? AND state=?", (state,code,remote_id,position,None if state=="dispatching" else to_rfc3339(self.clock()),self.channel_id,request_id,expected)).rowcount
            if changed:
                self.records._bump(c)
            return changed == 1

    def recover_interrupted(self):
        """Explicit startup recovery. Never dispatch pending operations after restart."""
        with self.records.transaction(write=True) as c:
            count = c.execute("UPDATE control_operations SET state='unknown',result_code='interrupted',finished_at=? WHERE channel_id=? AND state IN ('pending','dispatching')", (to_rfc3339(self.clock()),self.channel_id)).rowcount
            if count:
                self.records._bump(c)
            return count
