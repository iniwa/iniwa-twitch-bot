"""Channel-scoped community records, complete follower sync and body deletion."""

from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
import json
import math
import sqlite3
import unicodedata
from uuid import uuid4

from ...application.analytics import identifier
from ...application.community import ChannelEvent, ChatMessage, Follower, Person
from ...application.persistence import PersistenceError
from .sqlite import from_rfc3339, to_rfc3339, utc_now


def fact_id(*parts):
    return sha256(json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def normalized_name(value):
    """Stable display-name ordering without claiming Japanese reading order."""
    return unicodedata.normalize("NFKC", value or "").casefold()


class CommunityRepository:
    def __init__(self, database, channel_id, *, clock=utc_now):
        self.database = database
        self.channel_id = identifier(channel_id)
        self.clock = clock

    @contextmanager
    def transaction(self, *, write=False):
        c = None
        try:
            if write:
                c = self.database.connect()
                c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT OR IGNORE INTO community_state(channel_id) VALUES (?)", (self.channel_id,))
            else:
                c = sqlite3.connect(self.database.path.as_uri() + "?mode=ro", uri=True)
                c.row_factory = sqlite3.Row
                c.create_function("sort_name", 1, normalized_name, deterministic=True)
                c.execute("PRAGMA query_only=ON")
                c.execute("PRAGMA busy_timeout=5000")
                c.execute("BEGIN")
            yield c
            if write:
                c.commit()
        except sqlite3.Error as error:
            raise PersistenceError("community_unavailable", "community") from error
        finally:
            if c is not None:
                c.close()  # uncommitted failures roll back, including validation errors

    def _revision(self, c):
        row = c.execute("SELECT revision,follow_revision FROM community_state WHERE channel_id=?", (self.channel_id,)).fetchone()
        return tuple(row) if row else (0, 0)

    def _bump(self, c, *, follow=False):
        c.execute("UPDATE community_state SET revision=revision+1,follow_revision=follow_revision+? WHERE channel_id=?", (int(follow), self.channel_id))

    def _person(self, c, person, at):
        c.execute("INSERT INTO viewers(user_id,login,display_name,legacy_metadata_json,created_at,updated_at,revision) VALUES (?,?,?,'{}',?,?,1) ON CONFLICT(user_id) DO UPDATE SET login=COALESCE(excluded.login,login),display_name=COALESCE(excluded.display_name,display_name),updated_at=excluded.updated_at,revision=revision+1 WHERE updated_at<=excluded.updated_at", (person.user_id, person.login, person.display_name, at, at))
        c.execute("INSERT INTO community_people VALUES (?,?,?,?) ON CONFLICT(channel_id,user_id) DO UPDATE SET first_seen_at=MIN(first_seen_at,excluded.first_seen_at),last_seen_at=MAX(last_seen_at,excluded.last_seen_at)", (self.channel_id, person.user_id, at, at))

    def _validate_stream(self, c, stream_id, occurred_at):
        row = c.execute("SELECT * FROM streams WHERE id=? AND channel_id=?", (stream_id, self.channel_id)).fetchone()
        if row is None or occurred_at < row["started_at"] or (row["ended_at"] is not None and occurred_at >= row["ended_at"]):
            raise PersistenceError("event_outside_stream", "community")

    def record_event(self, event: ChannelEvent):
        occurred = to_rfc3339(event.occurred_at) if event.occurred_at else None
        received = to_rfc3339(event.received_at)
        user_id = event.person.user_id if event.person else None
        identity = (event.kind, user_id, occurred, event.stream_id, event.attribution, event.amount)
        with self.transaction(write=True) as c:
            old = c.execute("SELECT kind,user_id,occurred_at,stream_id,attribution,amount FROM channel_events WHERE channel_id=? AND id=?", (self.channel_id, event.id)).fetchone()
            if old is not None:
                if tuple(old) != identity:
                    raise PersistenceError("record_conflict", "community")
                return False
            if event.stream_id:
                self._validate_stream(c, event.stream_id, occurred)
            if event.kind == "follow" and c.execute("SELECT 1 FROM channel_events WHERE channel_id=? AND kind='follow' AND user_id=? AND occurred_at=?", (self.channel_id, user_id, occurred)).fetchone():
                return False
            if event.person:
                self._person(c, event.person, received)
            c.execute("INSERT INTO channel_events VALUES (?,?,?,?,?,?,?,?,?)", (self.channel_id, event.id, event.kind, user_id, occurred, received, event.stream_id, event.attribution, event.amount))
            if event.kind == "follow":
                self._follow(c, user_id, occurred, received, "event", event.stream_id)
            self._bump(c, follow=event.kind == "follow")
            return True

    def _follow(self, c, user_id, followed_at, detected_at, source, stream_id=None):
        key = fact_id(self.channel_id, user_id, followed_at)
        c.execute("INSERT OR IGNORE INTO follow_history VALUES (?,?,?,?,?,?,?,?)", (self.channel_id, key, user_id, "follow", followed_at, detected_at, source, stream_id))
        # A distinct, later Twitch follow timestamp is evidence of re-following.
        c.execute("UPDATE follow_history SET kind='refollow' WHERE channel_id=? AND user_id=? AND kind='follow' AND occurred_at>(SELECT MIN(occurred_at) FROM follow_history WHERE channel_id=? AND user_id=? AND kind IN ('follow','refollow'))", (self.channel_id, user_id, self.channel_id, user_id))
        if stream_id is not None:
            c.execute("UPDATE follow_history SET stream_id=? WHERE channel_id=? AND id=? AND stream_id IS NULL", (stream_id, self.channel_id, key))
        state = c.execute("SELECT * FROM follower_state WHERE channel_id=? AND user_id=?", (self.channel_id, user_id)).fetchone()
        if state is None or (source == "sync" and followed_at >= state["followed_at"]) or (source == "event" and followed_at > (state["evidence_at"] if state["status"] == "not_following" else state["followed_at"])):
            evidence = detected_at if source == "sync" else followed_at
            c.execute("INSERT INTO follower_state VALUES (?,?,'following',?,?) ON CONFLICT(channel_id,user_id) DO UPDATE SET status='following',followed_at=excluded.followed_at,evidence_at=excluded.evidence_at", (self.channel_id, user_id, followed_at, evidence))

    def record_chat(self, message: ChatMessage):
        if message.stream_id is None:
            return False  # no offline body or metadata is persisted
        occurred, received = to_rfc3339(message.occurred_at), to_rfc3339(message.received_at)
        with self.transaction(write=True) as c:
            old = c.execute("SELECT * FROM chat_messages WHERE channel_id=? AND id=?", (self.channel_id, message.id)).fetchone()
            if old is not None:
                if (old["user_id"], old["stream_id"], old["occurred_at"]) != (message.person.user_id, message.stream_id, occurred) or (old["body_deleted_at"] is None and old["body"] != message.body):
                    raise PersistenceError("record_conflict", "community")
                return False  # a deleted body must never be restored by redelivery
            self._validate_stream(c, message.stream_id, occurred)
            self._person(c, message.person, received)
            c.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?,?,?,NULL)", (self.channel_id, message.id, message.person.user_id, message.stream_id, occurred, received, message.body))
            c.execute("INSERT INTO viewer_streams VALUES (?,?,?,?,?,1) ON CONFLICT(channel_id,stream_id,user_id) DO UPDATE SET first_seen_at=MIN(first_seen_at,excluded.first_seen_at),last_seen_at=MAX(last_seen_at,excluded.last_seen_at),comment_count=comment_count+1", (self.channel_id, message.stream_id, message.person.user_id, occurred, occurred))
            self._bump(c)
            return True

    def start_sync(self, sync_id, expected_total):
        identifier(sync_id)
        if type(expected_total) is not int or not 0 <= expected_total <= 1_000_000:
            raise PersistenceError("invalid_total", "community")
        with self.transaction(write=True) as c:
            if c.execute("SELECT 1 FROM follower_sync_runs WHERE channel_id=? AND id=?", (self.channel_id, sync_id)).fetchone():
                raise PersistenceError("record_conflict", "community")
            c.execute("INSERT INTO follower_sync_runs VALUES (?,?,?,NULL,'collecting',?,?,'',0)", (self.channel_id, sync_id, to_rfc3339(self.clock()), self._revision(c)[1], expected_total))

    def _sync(self, c, sync_id):
        identifier(sync_id)
        row = c.execute("SELECT * FROM follower_sync_runs WHERE channel_id=? AND id=?", (self.channel_id, sync_id)).fetchone()
        if row is None:
            raise PersistenceError("sync_not_found", "community")
        return row

    def append_sync_page(self, sync_id, members, *, cursor="", next_cursor=None, total):
        if not isinstance(members, (tuple, list)) or len(members) > 1000 or any(not isinstance(m, Follower) for m in members):
            raise PersistenceError("invalid_page", "community")
        if not isinstance(cursor, str) or len(cursor) > 1000 or (next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 1000)):
            raise PersistenceError("invalid_cursor", "community")
        with self.transaction(write=True) as c:
            run = self._sync(c, sync_id)
            if run["state"] != "collecting" or run["next_cursor"] != cursor or type(total) is not int or total != run["expected_total"]:
                raise PersistenceError("incomplete_sync", "community")
            if next_cursor == cursor or (next_cursor is not None and c.execute("SELECT 1 FROM follower_sync_pages WHERE channel_id=? AND sync_id=? AND cursor=?", (self.channel_id, sync_id, next_cursor)).fetchone()):
                raise PersistenceError("cursor_cycle", "community")
            for member in members:
                followed = to_rfc3339(member.followed_at)
                if followed > run["started_at"]:
                    raise PersistenceError("sync_changed", "community")
                # Duplicate people across pages invalidate the snapshot, not a removal.
                if c.execute("SELECT 1 FROM follower_sync_members WHERE channel_id=? AND sync_id=? AND user_id=?", (self.channel_id, sync_id, member.person.user_id)).fetchone():
                    raise PersistenceError("duplicate_sync_member", "community")
                c.execute("INSERT INTO follower_sync_members VALUES (?,?,?,?,?,?)", (self.channel_id, sync_id, member.person.user_id, member.person.login, member.person.display_name, followed))
            count = c.execute("SELECT COUNT(*) FROM follower_sync_members WHERE channel_id=? AND sync_id=?", (self.channel_id, sync_id)).fetchone()[0]
            if count > total:
                raise PersistenceError("incomplete_sync", "community")
            c.execute("INSERT INTO follower_sync_pages VALUES (?,?,?,?)", (self.channel_id, sync_id, cursor, next_cursor))
            c.execute("UPDATE follower_sync_runs SET next_cursor=?,pages=pages+1 WHERE channel_id=? AND id=?", (next_cursor, self.channel_id, sync_id))

    def finish_sync(self, sync_id, *, success):
        if type(success) is not bool:
            raise PersistenceError("invalid_sync_result", "community")
        with self.transaction(write=True) as c:
            run = self._sync(c, sync_id)
            if run["state"] != "collecting":
                return run["state"]
            now = to_rfc3339(self.clock())
            if now < run["started_at"]:
                raise PersistenceError("invalid_timestamp", "community")
            state = "failed"
            if success:
                count = c.execute("SELECT COUNT(*) FROM follower_sync_members WHERE channel_id=? AND sync_id=?", (self.channel_id, sync_id)).fetchone()[0]
                if run["pages"] == 0 or run["next_cursor"] is not None or count != run["expected_total"]:
                    raise PersistenceError("incomplete_sync", "community")
                state = "complete" if self._revision(c)[1] == run["base_revision"] else "superseded"
                if state == "complete":
                    members = c.execute("SELECT * FROM follower_sync_members WHERE channel_id=? AND sync_id=?", (self.channel_id, sync_id))
                    for row in members:
                        self._person(c, Person(row["user_id"], row["login"], row["display_name"]), now)
                        self._follow(c, row["user_id"], row["followed_at"], now, "sync")
                    missing = c.execute("SELECT user_id FROM follower_state WHERE channel_id=? AND status='following' AND user_id NOT IN (SELECT user_id FROM follower_sync_members WHERE channel_id=? AND sync_id=?)", (self.channel_id, self.channel_id, sync_id)).fetchall()
                    for row in missing:
                        c.execute("INSERT INTO follow_history VALUES (?,?,?,'unfollow_detected',NULL,?,'sync',NULL)", (self.channel_id, fact_id(sync_id, row["user_id"], "unfollow"), row["user_id"], now))
                        c.execute("UPDATE follower_state SET status='not_following',evidence_at=? WHERE channel_id=? AND user_id=?", (now, self.channel_id, row["user_id"]))
                    self._bump(c, follow=True)
            c.execute("UPDATE follower_sync_runs SET state=?,finished_at=? WHERE channel_id=? AND id=?", (state, now, self.channel_id, sync_id))
            return state

    def _body_selection(self, c, start, end):
        digest, count = sha256(), 0
        for row in c.execute("SELECT id FROM chat_messages WHERE channel_id=? AND occurred_at>=? AND occurred_at<? AND body IS NOT NULL ORDER BY id", (self.channel_id, start, end)):
            digest.update(json.dumps(row[0], ensure_ascii=True).encode() + b"\n")
            count += 1
        return digest.hexdigest(), count

    def preview_body_deletion(self, start, end):
        first, last = to_rfc3339(start), to_rfc3339(end)
        if last <= first:
            raise PersistenceError("invalid_range", "community")
        now = self.clock()
        with self.transaction(write=True) as c:
            digest, count = self._body_selection(c, first, last)
            key = uuid4().hex
            expires = to_rfc3339(now + timedelta(minutes=30))
            c.execute("INSERT INTO chat_body_deletions VALUES (?,?,?,?,?,?,'preview',?,?,NULL)", (self.channel_id, key, first, last, digest, count, to_rfc3339(now), expires))
            return {"id": key, "range_start": first, "range_end": last, "message_count": count, "expires_at": expires, "state": "preview"}

    def delete_chat_bodies(self, preview_id):
        identifier(preview_id)
        with self.transaction(write=True) as c:
            row = c.execute("SELECT * FROM chat_body_deletions WHERE channel_id=? AND id=?", (self.channel_id, preview_id)).fetchone()
            if row is None:
                raise PersistenceError("preview_not_found", "community")
            if row["state"] == "applied":
                return {"id": preview_id, "state": "applied", "message_count": row["message_count"]}
            now = to_rfc3339(self.clock())
            if now >= row["expires_at"]:
                raise PersistenceError("preview_expired", "community")
            if self._body_selection(c, row["range_start"], row["range_end"]) != (row["selection_digest"], row["message_count"]):
                raise PersistenceError("preview_changed", "community")
            c.execute("UPDATE chat_messages SET body=NULL,body_deleted_at=? WHERE channel_id=? AND occurred_at>=? AND occurred_at<? AND body IS NOT NULL", (now, self.channel_id, row["range_start"], row["range_end"]))
            c.execute("UPDATE chat_body_deletions SET state='applied',applied_at=? WHERE channel_id=? AND id=?", (now, self.channel_id, preview_id))
            self._bump(c)
            return {"id": preview_id, "state": "applied", "message_count": row["message_count"]}

    def _page(self, c, query, params, *, limit, before, id_key, time_key=None,
              kind=None, sort=None, order="desc", sort_expression=None,
              filters=None, default_sort=None,
              revision_index=0):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PersistenceError("invalid_limit", "community")
        if time_key is not None and kind is None:
            legacy_params = tuple(params)
            total = c.execute(f"SELECT COUNT(*) FROM ({query})", legacy_params).fetchone()[0]
            where = ""
            if before is not None:
                if not isinstance(before, (tuple, list)) or len(before) != 2:
                    raise PersistenceError("invalid_cursor", "community")
                to_rfc3339(from_rfc3339(before[0]))
                identifier(before[1])
                where = f"WHERE ({time_key},{id_key})<(?,?)"
                legacy_params = (*legacy_params, *before)
            rows = [dict(row) for row in c.execute(
                f"SELECT * FROM ({query}) {where} ORDER BY {time_key} DESC,{id_key} DESC LIMIT ?",
                (*legacy_params, limit + 1))]
            last = rows[limit - 1] if len(rows) > limit else None
            return {"items": rows[:limit], "total": total,
                    "next_cursor": [last[time_key], last[id_key]] if last else None,
                    "data_revision": self._revision(c)[0]}
        if kind is None or sort is None or sort_expression is None:
            raise PersistenceError("invalid_sort", "community")
        if order not in ("asc", "desc"):
            raise PersistenceError("invalid_order", "community")
        filters = filters or {}
        revision = self._revision(c)[revision_index]
        total = c.execute(f"SELECT COUNT(*) FROM ({query})", params).fetchone()[0]
        ranked = f"SELECT q.*,{sort_expression} AS __sort_value FROM ({query}) q"
        where = ""
        if before is not None:
            if isinstance(before, (tuple, list)):
                # Compatibility is intentionally limited to the former default
                # two-value descending timestamp cursor.
                if len(before) != 2 or sort != default_sort or order != "desc":
                    raise PersistenceError("invalid_cursor", "community")
                to_rfc3339(from_rfc3339(before[0]))
                identifier(before[1])
                where = f"WHERE (__sort_value,{id_key})<(?,?)"
                params = (*params, *before)
            elif isinstance(before, dict):
                required = {"v", "kind", "channel", "sort", "order", "filters", "nulls", "value", "id", "revision"}
                if set(before) != required or before.get("v") != 1 or before.get("kind") != kind \
                        or before.get("channel") != self.channel_id or before.get("sort") != sort \
                        or before.get("order") != order or before.get("filters") != filters \
                        or before.get("nulls") != "last":
                    raise PersistenceError("invalid_cursor", "community")
                if type(before.get("revision")) is not int:
                    raise PersistenceError("invalid_cursor", "community")
                if before["revision"] != revision:
                    raise PersistenceError("list_changed", "community")
                cursor_id = identifier(before.get("id"))
                value = before.get("value")
                comparison = ">" if order == "asc" else "<"
                if value is None:
                    where = f"WHERE __sort_value IS NULL AND {id_key}{comparison}?"
                    params = (*params, cursor_id)
                elif isinstance(value, (str, int, float)) and not isinstance(value, bool) \
                        and (not isinstance(value, float) or math.isfinite(value)):
                    where = (f"WHERE __sort_value IS NULL OR "
                             f"(__sort_value IS NOT NULL AND (__sort_value{comparison}? OR "
                             f"(__sort_value=? AND {id_key}{comparison}?)))")
                    params = (*params, value, value, cursor_id)
                else:
                    raise PersistenceError("invalid_cursor", "community")
            else:
                raise PersistenceError("invalid_cursor", "community")
        direction = order.upper()
        rows = [dict(row) for row in c.execute(
            f"SELECT * FROM ({ranked}) ranked {where} "
            f"ORDER BY (__sort_value IS NULL) ASC,__sort_value {direction},{id_key} {direction} LIMIT ?",
            (*params, limit + 1))]
        last = rows[limit-1] if len(rows)>limit else None
        cursor = ({"v": 1, "kind": kind, "channel": self.channel_id, "sort": sort,
                   "order": order, "filters": filters, "nulls": "last",
                   "value": last["__sort_value"], "id": last[id_key],
                   "revision": revision} if last else None)
        items = rows[:limit]
        for row in items:
            row.pop("__sort_value", None)
        return {"items": items, "total": total, "next_cursor": cursor,
                "data_revision": revision, "sort": sort, "order": order}

    def events(self, *, stream_id=None, attribution=None, sort="sort_at", order="desc", limit=50, before=None):
        with self.transaction() as c:
            query = "SELECT e.*,v.display_name,v.login,COALESCE(e.occurred_at,e.received_at) AS sort_at FROM channel_events e LEFT JOIN viewers v ON v.user_id=e.user_id WHERE e.channel_id=?"
            params = [self.channel_id]
            if stream_id is not None:
                identifier(stream_id)
                query += " AND e.stream_id=?"
                params.append(stream_id)
            if attribution is not None:
                if attribution not in ("stream", "offline", "unknown"):
                    raise PersistenceError("invalid_attribution", "community")
                query += " AND e.attribution=?"
                params.append(attribution)
            sort_map = {"occurred_at": "q.occurred_at", "received_at": "q.received_at", "sort_at": "q.sort_at"}
            if sort not in sort_map:
                raise PersistenceError("invalid_sort", "community")
            filters = {"stream_id": stream_id or "", "attribution": attribution or ""}
            return self._page(c, query, params, limit=limit, before=before, kind="events",
                              sort=sort, order=order, sort_expression=sort_map[sort], id_key="id",
                              filters=filters, default_sort="sort_at")

    def _sync_information(self, c):
        latest = c.execute("SELECT id,state,started_at,finished_at FROM follower_sync_runs WHERE channel_id=? ORDER BY started_at DESC,id DESC LIMIT 1", (self.channel_id,)).fetchone()
        successful = c.execute("SELECT finished_at FROM follower_sync_runs WHERE channel_id=? AND state='complete' ORDER BY finished_at DESC,id DESC LIMIT 1", (self.channel_id,)).fetchone()
        return {"latest_sync": dict(latest) if latest else None,
                "last_successful_sync_at": successful["finished_at"] if successful else None,
                "sync_status": "unknown"}

    def followers(self, *, user_id=None, kind=None, sort="detected_at", order="desc", limit=50, before=None):
        with self.transaction() as c:
            query = "SELECT f.*,v.display_name,v.login FROM follow_history f JOIN viewers v ON v.user_id=f.user_id WHERE f.channel_id=?"
            params = [self.channel_id]
            if user_id is not None:
                identifier(user_id)
                query += " AND f.user_id=?"
                params.append(user_id)
            if kind is not None:
                if kind not in ("follow", "refollow", "unfollow_detected"):
                    raise PersistenceError("invalid_kind", "community")
                query += " AND f.kind=?"
                params.append(kind)
            sort_map = {"occurred_at": "q.occurred_at", "detected_at": "q.detected_at",
                        "name": "sort_name(COALESCE(NULLIF(q.display_name,''),NULLIF(q.login,''),q.user_id))"}
            if sort not in sort_map:
                raise PersistenceError("invalid_sort", "community")
            filters = {"user_id": user_id or "", "kind": kind or ""}
            result = self._page(c, query, params, limit=limit, before=before, kind="followers",
                                sort=sort, order=order, sort_expression=sort_map[sort], id_key="id",
                                filters=filters, default_sort="detected_at")
            result.update(self._sync_information(c))
            return result

    def follower_status(self, *, status=None, sort="evidence_at", order="desc", limit=50, before=None):
        with self.transaction() as c:
            query = "SELECT p.user_id,v.login,v.display_name,COALESCE(s.status,'unknown') AS status,s.followed_at,s.evidence_at FROM community_people p JOIN viewers v ON v.user_id=p.user_id LEFT JOIN follower_state s ON s.channel_id=p.channel_id AND s.user_id=p.user_id WHERE p.channel_id=?"
            params = [self.channel_id]
            if status is not None:
                if status not in ("following", "not_following", "unknown"):
                    raise PersistenceError("invalid_status", "community")
                if status == "unknown":
                    query += " AND s.status IS NULL"
                else:
                    query += " AND s.status=?"
                    params.append(status)
            sort_map = {"followed_at": "q.followed_at", "evidence_at": "q.evidence_at",
                        "name": "sort_name(COALESCE(NULLIF(q.display_name,''),NULLIF(q.login,''),q.user_id))"}
            if sort not in sort_map:
                raise PersistenceError("invalid_sort", "community")
            result = self._page(c, query, params, limit=limit, before=before, kind="follower_status",
                                sort=sort, order=order, sort_expression=sort_map[sort], id_key="user_id",
                                filters={"status": status or ""}, default_sort="evidence_at")
            result.update(self._sync_information(c))
            return result

    def people(self, *, stream_id=None, follow_status=None, sort="last_seen_at", order="desc", limit=50, before=None):
        with self.transaction() as c:
            query = "SELECT p.user_id,p.first_seen_at,p.last_seen_at,v.login,v.display_name,s.status AS follow_status,(SELECT COUNT(*) FROM viewer_streams vs WHERE vs.channel_id=p.channel_id AND vs.user_id=p.user_id) AS recorded_streams FROM community_people p JOIN viewers v ON v.user_id=p.user_id LEFT JOIN follower_state s ON s.channel_id=p.channel_id AND s.user_id=p.user_id WHERE p.channel_id=?"
            params = [self.channel_id]
            if stream_id is not None:
                identifier(stream_id)
                query += " AND EXISTS(SELECT 1 FROM viewer_streams vs WHERE vs.channel_id=p.channel_id AND vs.user_id=p.user_id AND vs.stream_id=?)"
                params.append(stream_id)
            if follow_status is not None:
                if follow_status not in ("following", "not_following", "unknown"):
                    raise PersistenceError("invalid_status", "community")
                if follow_status == "unknown":
                    query += " AND s.status IS NULL"
                else:
                    query += " AND s.status=?"
                    params.append(follow_status)
            sort_map = {"first_seen_at": "q.first_seen_at", "last_seen_at": "q.last_seen_at",
                        "recorded_streams": "q.recorded_streams",
                        "name": "sort_name(COALESCE(NULLIF(q.display_name,''),NULLIF(q.login,''),q.user_id))"}
            if sort not in sort_map:
                raise PersistenceError("invalid_sort", "community")
            return self._page(c, query, params, limit=limit, before=before, kind="people",
                              sort=sort, order=order, sort_expression=sort_map[sort], id_key="user_id",
                              filters={"stream_id": stream_id or "", "follow_status": follow_status or ""},
                              default_sort="last_seen_at")

    def person(self, user_id, *, sort="last_seen_at", order="desc", limit=50, before=None):
        identifier(user_id)
        with self.transaction() as c:
            row = c.execute("SELECT v.user_id,v.login,v.display_name,v.note,s.status AS follow_status,s.followed_at,s.evidence_at FROM community_people p JOIN viewers v ON v.user_id=p.user_id LEFT JOIN follower_state s ON s.channel_id=p.channel_id AND s.user_id=p.user_id WHERE p.channel_id=? AND p.user_id=?", (self.channel_id, user_id)).fetchone()
            if row is None:
                raise PersistenceError("person_not_found", "community")
            query = "SELECT vs.*,s.title FROM viewer_streams vs JOIN streams s ON s.id=vs.stream_id WHERE vs.channel_id=? AND vs.user_id=?"
            sort_map = {"first_seen_at": "q.first_seen_at", "last_seen_at": "q.last_seen_at",
                        "comment_count": "q.comment_count"}
            if sort not in sort_map:
                raise PersistenceError("invalid_sort", "community")
            result = self._page(c, query, [self.channel_id,user_id], limit=limit, before=before,
                                kind="person", sort=sort, order=order,
                                sort_expression=sort_map[sort], id_key="stream_id",
                                filters={"user_id": user_id}, default_sort="last_seen_at")
            result["person"] = dict(row)
            return result

    def chats(self, *, stream_id=None, sort="occurred_at", order="desc", limit=50, before=None):
        with self.transaction() as c:
            query = "SELECT m.*,v.login,v.display_name FROM chat_messages m JOIN viewers v ON v.user_id=m.user_id WHERE m.channel_id=?"
            params = [self.channel_id]
            if stream_id is not None:
                identifier(stream_id)
                query += " AND m.stream_id=?"
                params.append(stream_id)
            if sort != "occurred_at":
                raise PersistenceError("invalid_sort", "community")
            return self._page(c, query, params, limit=limit, before=before, kind="chats",
                              sort=sort, order=order, sort_expression="q.occurred_at", id_key="id",
                              filters={"stream_id": stream_id or ""}, default_sort="occurred_at")
