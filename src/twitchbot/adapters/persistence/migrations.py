"""Forward-only, checksummed v2 SQLite migrations."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n".join((str(self.version), self.name, *self.statements)).encode("utf-8")
        return sha256(payload).hexdigest()


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "core_system", (
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY CHECK(version > 0), name TEXT NOT NULL, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL CHECK(json_valid(value_json)), revision INTEGER NOT NULL CHECK(revision >= 0), updated_at TEXT NOT NULL)",
        "CREATE TABLE channel_read_model (channel_id TEXT PRIMARY KEY, title TEXT NOT NULL, game_id TEXT, game_name TEXT, tags_json TEXT NOT NULL CHECK(json_valid(tags_json)), active_preset_id TEXT, observed_at TEXT NOT NULL, source TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 0))",
        "CREATE TABLE operation_log (id TEXT PRIMARY KEY, operation_type TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL, state TEXT NOT NULL, message_code TEXT NOT NULL, request_id TEXT, started_at TEXT NOT NULL, finished_at TEXT, safe_details_json TEXT NOT NULL CHECK(json_valid(safe_details_json)))",
        "CREATE TABLE processed_event_ids (message_id TEXT PRIMARY KEY, message_type TEXT NOT NULL, received_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
        "CREATE INDEX processed_event_ids_expires_at_idx ON processed_event_ids(expires_at)",
        "CREATE TABLE import_batches (id TEXT PRIMARY KEY, importer_version TEXT NOT NULL, imported_at TEXT NOT NULL, cutoff_at TEXT NOT NULL, source_manifest_json TEXT NOT NULL CHECK(json_valid(source_manifest_json)), source_base_reference TEXT NOT NULL, result TEXT NOT NULL, report_reference TEXT)",
    )),
    Migration(2, "domain_destination", (
        "CREATE TABLE streams (id TEXT PRIMARY KEY CHECK(length(id)>0), channel_id TEXT NOT NULL CHECK(length(channel_id)>0), title TEXT NOT NULL, game_id TEXT, game_name TEXT, thumbnail_url TEXT, tags_json TEXT NOT NULL CHECK(json_valid(tags_json) AND json_type(tags_json)='array'), started_at TEXT NOT NULL, ended_at TEXT, duration_seconds INTEGER CHECK(duration_seconds IS NULL OR (typeof(duration_seconds)='integer' AND duration_seconds>=0)), source TEXT NOT NULL CHECK(source IN ('bot','api','imported')), completeness TEXT NOT NULL CHECK(completeness IN ('full','samples_only','metadata_only','partial')), max_viewers INTEGER CHECK(max_viewers IS NULL OR (typeof(max_viewers)='integer' AND max_viewers>=0)), average_viewers REAL CHECK(average_viewers IS NULL OR (typeof(average_viewers) IN ('integer','real') AND average_viewers>=0)), follower_count INTEGER CHECK(follower_count IS NULL OR (typeof(follower_count)='integer' AND follower_count>=0)), total_comments INTEGER CHECK(total_comments IS NULL OR (typeof(total_comments)='integer' AND total_comments>=0)), legacy_metadata_json TEXT NOT NULL CHECK(json_valid(legacy_metadata_json) AND json_type(legacy_metadata_json)='object'), import_batch_id TEXT REFERENCES import_batches(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=0))",
        "CREATE INDEX streams_started_at_id_idx ON streams(started_at, id)",
        "CREATE TABLE stream_samples (stream_id TEXT NOT NULL REFERENCES streams(id) ON DELETE CASCADE, sampled_at TEXT NOT NULL, viewer_count INTEGER CHECK(viewer_count IS NULL OR (typeof(viewer_count)='integer' AND viewer_count>=0)), chat_count INTEGER CHECK(chat_count IS NULL OR (typeof(chat_count)='integer' AND chat_count>=0)), messages_per_minute REAL CHECK(messages_per_minute IS NULL OR ((typeof(messages_per_minute) IN ('integer','real')) AND messages_per_minute>=0)), bits INTEGER CHECK(bits IS NULL OR (typeof(bits)='integer' AND bits>=0)), gift_subscriptions INTEGER CHECK(gift_subscriptions IS NULL OR (typeof(gift_subscriptions)='integer' AND gift_subscriptions>=0)), follower_total INTEGER CHECK(follower_total IS NULL OR (typeof(follower_total)='integer' AND follower_total>=0)), PRIMARY KEY(stream_id,sampled_at))",
        "CREATE TABLE viewers (user_id TEXT PRIMARY KEY CHECK(length(user_id)>0), login TEXT, display_name TEXT, followed_at TEXT, unfollowed_at TEXT, visit_count INTEGER CHECK(visit_count IS NULL OR (typeof(visit_count)='integer' AND visit_count>=0)), watch_seconds INTEGER CHECK(watch_seconds IS NULL OR (typeof(watch_seconds)='integer' AND watch_seconds>=0)), comment_count INTEGER CHECK(comment_count IS NULL OR (typeof(comment_count)='integer' AND comment_count>=0)), bits_total INTEGER CHECK(bits_total IS NULL OR (typeof(bits_total)='integer' AND bits_total>=0)), is_subscriber INTEGER CHECK(is_subscriber IS NULL OR is_subscriber IN (0,1)), sub_months INTEGER CHECK(sub_months IS NULL OR (typeof(sub_months)='integer' AND sub_months>=0)), last_sub_at TEXT, last_sub_plan TEXT, gifts_given INTEGER CHECK(gifts_given IS NULL OR (typeof(gifts_given)='integer' AND gifts_given>=0)), gifts_received INTEGER CHECK(gifts_received IS NULL OR (typeof(gifts_received)='integer' AND gifts_received>=0)), streak INTEGER CHECK(streak IS NULL OR (typeof(streak)='integer' AND streak>=0)), last_seen_at TEXT, last_stream_id TEXT, note TEXT, legacy_metadata_json TEXT NOT NULL CHECK(json_valid(legacy_metadata_json) AND json_type(legacy_metadata_json)='object'), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=0))",
        "CREATE INDEX viewers_login_idx ON viewers(login)",
        "CREATE INDEX viewers_last_seen_idx ON viewers(last_seen_at)",
        "CREATE TABLE vod_assets (id TEXT PRIMARY KEY CHECK(length(id)>0), stream_id TEXT NOT NULL UNIQUE REFERENCES streams(id) ON DELETE CASCADE, twitch_vod_id TEXT, relative_path TEXT CHECK(relative_path IS NULL OR (length(relative_path)>0 AND relative_path NOT LIKE '/%' AND relative_path NOT LIKE '%\\%' AND relative_path NOT LIKE '%//%' AND relative_path NOT IN ('.','..') AND relative_path NOT LIKE './%' AND relative_path NOT LIKE '../%' AND relative_path NOT LIKE '%/./%' AND relative_path NOT LIKE '%/../%' AND relative_path NOT LIKE '%/.' AND relative_path NOT LIKE '%/..')), size_bytes INTEGER CHECK(size_bytes IS NULL OR (typeof(size_bytes)='integer' AND size_bytes>=0)), discovered_at TEXT, verified_at TEXT, remote_state TEXT NOT NULL CHECK(length(remote_state)>0), local_state TEXT NOT NULL CHECK(length(local_state)>0), revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=0))",
    )),
    Migration(3, "viewer_observations", (
        "CREATE TABLE stream_metric_state (stream_id TEXT PRIMARY KEY REFERENCES streams(id) ON DELETE CASCADE, revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0), end_precision TEXT NOT NULL DEFAULT 'unknown' CHECK(end_precision IN ('confirmed','estimated','unknown')))",
        "CREATE TABLE collection_runs (stream_id TEXT NOT NULL REFERENCES streams(id) ON DELETE CASCADE, id TEXT NOT NULL CHECK(length(id)>0), started_at TEXT NOT NULL, stopped_at TEXT CHECK(stopped_at IS NULL OR stopped_at>=started_at), PRIMARY KEY(stream_id,id))",
        "CREATE INDEX collection_runs_time_idx ON collection_runs(stream_id,started_at)",
        "CREATE TABLE viewer_observations (stream_id TEXT NOT NULL, run_id TEXT NOT NULL, observed_at TEXT NOT NULL, viewer_count INTEGER NOT NULL CHECK(typeof(viewer_count)='integer' AND viewer_count>=0), PRIMARY KEY(stream_id,observed_at), FOREIGN KEY(stream_id,run_id) REFERENCES collection_runs(stream_id,id) ON DELETE CASCADE)",
        "CREATE TABLE observation_gaps (stream_id TEXT NOT NULL REFERENCES streams(id) ON DELETE CASCADE, id TEXT NOT NULL CHECK(length(id)>0), started_at TEXT NOT NULL, ended_at TEXT CHECK(ended_at IS NULL OR ended_at>started_at), reason TEXT NOT NULL CHECK(reason IN ('request_failed','stopped','disconnected','unknown')), PRIMARY KEY(stream_id,id))",
        "CREATE INDEX observation_gaps_time_idx ON observation_gaps(stream_id,started_at)",
    ) + tuple(
        f"CREATE TRIGGER {table}_{event.lower()}_revision AFTER {event} ON {table} BEGIN INSERT INTO stream_metric_state(stream_id,revision) SELECT {ref}.stream_id,1 WHERE EXISTS(SELECT 1 FROM streams WHERE id={ref}.stream_id) ON CONFLICT(stream_id) DO UPDATE SET revision=revision+1; END"
        for table in ("collection_runs", "viewer_observations", "observation_gaps")
        for event, ref in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD"))
    )),
    Migration(4, "community_recording", (
        "CREATE TABLE community_state (channel_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0), follow_revision INTEGER NOT NULL DEFAULT 0 CHECK(follow_revision>=0))",
        "CREATE TABLE community_people (channel_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES viewers(user_id), first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, PRIMARY KEY(channel_id,user_id))",
        "CREATE TABLE channel_events (channel_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('follow','subscribe','resubscribe','gift_subscription','cheer','raid','redemption','prediction')), user_id TEXT REFERENCES viewers(user_id), occurred_at TEXT, received_at TEXT NOT NULL, stream_id TEXT REFERENCES streams(id), attribution TEXT NOT NULL CHECK(attribution IN ('stream','offline','unknown')), amount INTEGER CHECK(amount IS NULL OR (typeof(amount)='integer' AND amount>=0)), PRIMARY KEY(channel_id,id), CHECK((attribution='stream' AND stream_id IS NOT NULL) OR (attribution<>'stream' AND stream_id IS NULL)))",
        "CREATE INDEX channel_events_time_idx ON channel_events(channel_id,received_at,id)",
        "CREATE INDEX channel_events_stream_idx ON channel_events(channel_id,stream_id,received_at,id)",
        "CREATE TABLE follow_history (channel_id TEXT NOT NULL, id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES viewers(user_id), kind TEXT NOT NULL CHECK(kind IN ('follow','refollow','unfollow_detected')), occurred_at TEXT, detected_at TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('event','sync')), stream_id TEXT REFERENCES streams(id), PRIMARY KEY(channel_id,id))",
        "CREATE INDEX follow_history_person_idx ON follow_history(channel_id,user_id,detected_at,id)",
        "CREATE INDEX follow_history_time_idx ON follow_history(channel_id,detected_at,id)",
        "CREATE TABLE follower_state (channel_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES viewers(user_id), status TEXT NOT NULL CHECK(status IN ('following','not_following')), followed_at TEXT NOT NULL, evidence_at TEXT NOT NULL, PRIMARY KEY(channel_id,user_id))",
        "CREATE TABLE follower_sync_runs (channel_id TEXT NOT NULL, id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, state TEXT NOT NULL CHECK(state IN ('collecting','complete','failed','superseded')), base_revision INTEGER NOT NULL, expected_total INTEGER NOT NULL CHECK(expected_total>=0), next_cursor TEXT, pages INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(channel_id,id))",
        "CREATE TABLE follower_sync_pages (channel_id TEXT NOT NULL, sync_id TEXT NOT NULL, cursor TEXT NOT NULL, next_cursor TEXT, PRIMARY KEY(channel_id,sync_id,cursor), FOREIGN KEY(channel_id,sync_id) REFERENCES follower_sync_runs(channel_id,id) ON DELETE CASCADE)",
        "CREATE TABLE follower_sync_members (channel_id TEXT NOT NULL, sync_id TEXT NOT NULL, user_id TEXT NOT NULL, login TEXT, display_name TEXT, followed_at TEXT NOT NULL, PRIMARY KEY(channel_id,sync_id,user_id), FOREIGN KEY(channel_id,sync_id) REFERENCES follower_sync_runs(channel_id,id) ON DELETE CASCADE)",
        "CREATE TABLE chat_messages (channel_id TEXT NOT NULL, id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES viewers(user_id), stream_id TEXT NOT NULL REFERENCES streams(id), occurred_at TEXT NOT NULL, received_at TEXT NOT NULL, body TEXT, body_deleted_at TEXT, PRIMARY KEY(channel_id,id), CHECK((body IS NOT NULL AND body_deleted_at IS NULL) OR (body IS NULL AND body_deleted_at IS NOT NULL)))",
        "CREATE INDEX chat_messages_time_idx ON chat_messages(channel_id,occurred_at,id)",
        "CREATE INDEX chat_messages_stream_idx ON chat_messages(channel_id,stream_id,occurred_at,id)",
        "CREATE TABLE viewer_streams (channel_id TEXT NOT NULL, stream_id TEXT NOT NULL REFERENCES streams(id), user_id TEXT NOT NULL REFERENCES viewers(user_id), first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, comment_count INTEGER NOT NULL CHECK(comment_count>0), PRIMARY KEY(channel_id,stream_id,user_id))",
        "CREATE INDEX viewer_streams_person_idx ON viewer_streams(channel_id,user_id,last_seen_at,stream_id)",
        "CREATE TABLE chat_body_deletions (channel_id TEXT NOT NULL, id TEXT NOT NULL, range_start TEXT NOT NULL, range_end TEXT NOT NULL, selection_digest TEXT NOT NULL, message_count INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('preview','applied')), created_at TEXT NOT NULL, expires_at TEXT NOT NULL, applied_at TEXT, PRIMARY KEY(channel_id,id))",
    )),
    Migration(5, "local_live_controls", (
        "CREATE TABLE channel_presets (channel_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL, game_id TEXT, game_name TEXT, tags_json TEXT NOT NULL CHECK(json_valid(tags_json)), social_tags_json TEXT NOT NULL CHECK(json_valid(social_tags_json)), revision INTEGER NOT NULL CHECK(revision>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(channel_id,id))",
        "CREATE TABLE stream_notes (channel_id TEXT NOT NULL, id TEXT NOT NULL, stream_id TEXT NOT NULL REFERENCES streams(id), occurred_at TEXT NOT NULL, body TEXT NOT NULL, marker_requested INTEGER NOT NULL CHECK(marker_requested IN (0,1)), revision INTEGER NOT NULL CHECK(revision>0), created_at TEXT NOT NULL, PRIMARY KEY(channel_id,id))",
        "CREATE INDEX stream_notes_time_idx ON stream_notes(channel_id,stream_id,occurred_at,id)",
        "CREATE TABLE preset_previews (channel_id TEXT NOT NULL, id TEXT NOT NULL, preset_id TEXT NOT NULL, preset_revision INTEGER NOT NULL, channel_revision INTEGER NOT NULL, expires_at TEXT NOT NULL, PRIMARY KEY(channel_id,id), FOREIGN KEY(channel_id,preset_id) REFERENCES channel_presets(channel_id,id))",
        "CREATE TABLE control_operations (channel_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('marker','preset')), target_id TEXT NOT NULL, preview_id TEXT, state TEXT NOT NULL CHECK(state IN ('pending','dispatching','succeeded','partial','failed','unknown','unavailable','no_change')), result_code TEXT NOT NULL, remote_id TEXT, position_seconds INTEGER, created_at TEXT NOT NULL, finished_at TEXT, PRIMARY KEY(channel_id,id), UNIQUE(channel_id,preview_id))",
        "CREATE TABLE person_notes (channel_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES viewers(user_id), body TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0), updated_at TEXT NOT NULL, PRIMARY KEY(channel_id,user_id))",
    )),
    Migration(6, "backup_scheduling", (
        "CREATE TABLE backup_policy (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), daily_hour INTEGER NOT NULL CHECK(daily_hour BETWEEN 0 AND 23), revision INTEGER NOT NULL CHECK(revision>0))",
        "CREATE TABLE backup_jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed','unknown')), backup_id TEXT, result_code TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT)",
        "CREATE INDEX backup_jobs_pending_idx ON backup_jobs(state,created_at,id)",
    )),
    Migration(7, "eventsub_recording_coverage", (
        "CREATE TABLE stream_presence (channel_id TEXT NOT NULL, observed_at TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('live','offline')), stream_id TEXT REFERENCES streams(id), PRIMARY KEY(channel_id,observed_at), CHECK((state='live' AND stream_id IS NOT NULL) OR (state='offline' AND stream_id IS NULL)))",
        "CREATE TABLE eventsub_gaps (channel_id TEXT NOT NULL, id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, reason TEXT NOT NULL CHECK(reason IN ('disconnected','stopped','authorization','invalid_event')), PRIMARY KEY(channel_id,id))",
        "CREATE INDEX eventsub_gaps_time_idx ON eventsub_gaps(channel_id,started_at)",
    )),
    Migration(8, "chat_automation", (
        "CREATE TABLE automation_policy (channel_id TEXT PRIMARY KEY, commands_enabled INTEGER NOT NULL DEFAULT 0 CHECK(commands_enabled IN (0,1)), posts_enabled INTEGER NOT NULL DEFAULT 0 CHECK(posts_enabled IN (0,1)), ignored_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(ignored_json)), revision INTEGER NOT NULL CHECK(revision>0))",
        "CREATE TABLE automation_definitions (channel_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('command','post')), name TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), specification_json TEXT NOT NULL CHECK(json_valid(specification_json)), revision INTEGER NOT NULL CHECK(revision>0), execution_revision INTEGER NOT NULL CHECK(execution_revision>0), position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(channel_id,id))",
        "CREATE TABLE command_aliases (channel_id TEXT NOT NULL, name TEXT NOT NULL, definition_id TEXT NOT NULL, PRIMARY KEY(channel_id,name), FOREIGN KEY(channel_id,definition_id) REFERENCES automation_definitions(channel_id,id) ON DELETE CASCADE)",
        "CREATE TABLE chat_dispatches (channel_id TEXT NOT NULL, id TEXT NOT NULL, definition_id TEXT NOT NULL, definition_revision INTEGER NOT NULL, kind TEXT NOT NULL, stream_id TEXT NOT NULL, user_id TEXT, role INTEGER, source_message_id TEXT, state TEXT NOT NULL CHECK(state IN ('pending','dispatching','sent','skipped','failed','unknown')), reason TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, finished_at TEXT, PRIMARY KEY(channel_id,id), UNIQUE(channel_id,source_message_id))",
        "CREATE INDEX chat_dispatches_pending_idx ON chat_dispatches(channel_id,state,created_at)",
        "CREATE TABLE command_cooldowns (channel_id TEXT NOT NULL, definition_id TEXT NOT NULL, user_id TEXT NOT NULL, available_at TEXT NOT NULL, PRIMARY KEY(channel_id,definition_id,user_id))",
        "CREATE TABLE post_waits (channel_id TEXT NOT NULL, definition_id TEXT NOT NULL, execution_revision INTEGER NOT NULL, stream_id TEXT NOT NULL, category_id TEXT, started_at TEXT NOT NULL, comments INTEGER NOT NULL DEFAULT 0, held INTEGER NOT NULL DEFAULT 0 CHECK(held IN (0,1)), PRIMARY KEY(channel_id,definition_id))",
        "CREATE TABLE automation_messages (channel_id TEXT NOT NULL, id TEXT NOT NULL, received_at TEXT NOT NULL, PRIMARY KEY(channel_id,id))",
    )),
    Migration(9, "manual_predictions", (
        "CREATE TABLE prediction_policy (channel_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), revision INTEGER NOT NULL CHECK(revision>0))",
        "CREATE TABLE prediction_presets (channel_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, specification_json TEXT NOT NULL CHECK(json_valid(specification_json)), revision INTEGER NOT NULL CHECK(revision>0), PRIMARY KEY(channel_id,id))",
        "CREATE TABLE prediction_cache (channel_id TEXT PRIMARY KEY, items_json TEXT NOT NULL CHECK(json_valid(items_json)), observed_at TEXT NOT NULL)",
        "CREATE TABLE prediction_operations (channel_id TEXT NOT NULL, id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('start','lock','resolve','cancel')), payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), stream_id TEXT REFERENCES streams(id), state TEXT NOT NULL CHECK(state IN ('preview','pending','dispatching','succeeded','failed','unknown','expired')), remote_id TEXT, result_code TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, finished_at TEXT, PRIMARY KEY(channel_id,id))",
        "CREATE INDEX prediction_operations_pending_idx ON prediction_operations(channel_id,state,created_at)",
    )),
    Migration(10, "restore_candidates", (
        "CREATE TABLE restore_jobs (id TEXT PRIMARY KEY, backup_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','running','verified','failed','unknown')), candidate_name TEXT, result_code TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT)",
        "CREATE INDEX restore_jobs_pending_idx ON restore_jobs(state,created_at,id)",
    )),
    Migration(11, "dashboard_sort_metadata", (
        "CREATE TABLE automation_definition_times (channel_id TEXT NOT NULL, definition_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(channel_id,definition_id), FOREIGN KEY(channel_id,definition_id) REFERENCES automation_definitions(channel_id,id) ON DELETE CASCADE)",
        "CREATE INDEX automation_definition_times_updated_idx ON automation_definition_times(channel_id,updated_at,definition_id)",
        "CREATE TABLE prediction_preset_times (channel_id TEXT NOT NULL, preset_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(channel_id,preset_id), FOREIGN KEY(channel_id,preset_id) REFERENCES prediction_presets(channel_id,id) ON DELETE CASCADE)",
        "CREATE INDEX prediction_preset_times_updated_idx ON prediction_preset_times(channel_id,updated_at,preset_id)",
    )),
)
