"""Candidate-only, transactional importer for a previously inspected source.

This module deliberately has no command or application wiring.  It accepts an
explicit disposable database and is intentionally conservative: anything it
cannot represent faithfully is counted as deferred rather than guessed.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from ..adapters.persistence.sqlite import DEFAULT_DATABASE_PATH, SQLiteDatabase, to_rfc3339, utc_now
from ..adapters.persistence.migrations import MIGRATIONS
from ..application.persistence import PersistenceError
from .inspector import InspectionError, InspectionReport, LegacySourceInspector, _STREAM, _json

_VERSION = "candidate-importer-v2"
_VIEWER_COLUMNS = 'user_id,login,display_name,followed_at,unfollowed_at,visit_count,watch_seconds,comment_count,bits_total,is_subscriber,sub_months,gifts_given,gifts_received,streak,last_sub_at,last_sub_plan,last_seen_at,last_stream_id,note,legacy_metadata_json'


class CandidateImportError(RuntimeError):
    """Safe typed failure.  Never expose source values or candidate paths."""
    def __init__(self, code: str, context: str = "candidate_import") -> None:
        self.code = code
        self.context = context
        super().__init__(f"candidate import error ({code}) in {context}")


@dataclass(frozen=True, slots=True)
class CandidateImportReport:
    source_reference: str
    batch_id: str
    cutoff: str
    result: str
    manifest: tuple[tuple[str, int, str], ...]
    entity_counts: tuple[tuple[str, int, int, int, int], ...]
    aggregates: tuple[tuple[str, int | float], ...]
    deferred_counts: tuple[tuple[str, int], ...]
    vod_counts: tuple[tuple[str, int], ...]
    source_unchanged: bool = True
    credentials_redacted: bool = True

    def to_safe_mapping(self) -> dict[str, object]:
        return {
            "importer_version": _VERSION, "source_reference": self.source_reference,
            "batch_id": self.batch_id, "cutoff": self.cutoff, "result": self.result,
            "manifest": [{"name": n, "size": s, "checksum": c} for n, s, c in self.manifest],
            "entities": [{"entity": e, "read": r, "imported": i, "skipped": s, "rejected": x}
                         for e, r, i, s, x in self.entity_counts],
            "aggregates": dict(self.aggregates), "deferred": dict(self.deferred_counts),
            "vod_path_counts": dict(self.vod_counts), "source_unchanged": True,
            "credentials_redacted": True,
        }


def _aware(value: object, *, epoch: bool = False) -> datetime | None:
    if epoch and type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value):
        try: return datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError): return None
    if type(value) is not str: return None
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: return None
    if parsed.tzinfo is None or parsed.utcoffset() is None: return None
    return parsed.astimezone(timezone.utc)


def _integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _viewer_dates(record):
    dates, day_only = [], {}
    for key in ('followed_at','unfollowed_at','last_sub_ts','last_seen_ts'):
        raw=record.get(key)
        if raw is None or raw == '':
            dates.append(None); continue
        if key in ('followed_at','unfollowed_at') and type(raw) is str and len(raw)==10:
            try:
                if date.fromisoformat(raw).isoformat()!=raw: raise ValueError
            except ValueError:
                return None, None
            day_only[key]=raw
            dates.append(None); continue
        parsed=_aware(raw,epoch=key in ('last_sub_ts','last_seen_ts'))
        if parsed is None: return None, None
        dates.append(parsed)
    return dates, day_only


def _finite(value: object) -> float | None:
    return float(value) if type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _duration(value: object) -> int | None:
    if type(value) is not str: return None
    fields = value.split(":")
    if len(fields) not in (2, 3) or any(not f.isascii() or not f.isdecimal() for f in fields): return None
    numbers = [int(f) for f in fields]
    if any(n < 0 for n in numbers) or numbers[-1] >= 60 or numbers[-2] >= 60: return None
    return numbers[-1] + 60 * numbers[-2] + (3600 * numbers[0] if len(numbers) == 3 else 0)


class CandidateImporter:
    def __init__(self, source_root: str | Path, downloads_root: str | Path, source_reference: str,
                 database: SQLiteDatabase, *, clock: Callable[[], datetime] = utc_now) -> None:
        if not isinstance(database, SQLiteDatabase): raise CandidateImportError("invalid_candidate_database", "constructor")
        if database.path == Path(DEFAULT_DATABASE_PATH): raise CandidateImportError("default_database_forbidden", "constructor")
        if not callable(clock): raise CandidateImportError("invalid_callable", "constructor")
        # Inspector construction deliberately performs only lexical validation.
        try: self._inspector = LegacySourceInspector(source_root, downloads_root, source_reference, clock=clock)
        except InspectionError as exc: raise CandidateImportError(exc.code, "constructor") from exc
        self._database, self._clock = database, clock

    @staticmethod
    def _semantic_equal(one: InspectionReport, other: InspectionReport) -> bool:
        return (one.source_reference, one.manifest, one.unsupported, one.documents, one.issues,
                one.unknown_fields, one.credentials, one.vod_counts, one.source_unchanged,
                one.credentials_redacted) == (other.source_reference, other.manifest, other.unsupported,
                other.documents, other.issues, other.unknown_fields, other.credentials, other.vod_counts,
                other.source_unchanged, other.credentials_redacted)

    def _validate_report(self, report: InspectionReport) -> InspectionReport:
        if not isinstance(report, InspectionReport) or report.source_reference != self._inspector._reference:
            raise CandidateImportError("invalid_report", "report")
        try:
            self._inspector.verify_unchanged(report)
            fresh = self._inspector.inspect()
        except InspectionError as exc: raise CandidateImportError(exc.code, "source") from exc
        if not self._semantic_equal(report, fresh): raise CandidateImportError("report_mismatch", "report")
        if any(state == "configured" for _, _, state in report.credentials):
            raise CandidateImportError("credential_validation_required", "credentials")
        # The accepted inspection is the migration authority.  A fresh pass is
        # only a semantic/source validation: its clock-derived cutoff must not
        # create a different candidate batch on a later retry.
        return report

    @staticmethod
    def _manifest(report: InspectionReport) -> tuple[tuple[str, int, str], ...]:
        return tuple((e.name, e.size, e.checksum) for e in report.manifest)

    @staticmethod
    def _batch_id(report: InspectionReport) -> str:
        safe = {"v": _VERSION, "source": report.source_reference, "cutoff": report.cutoff,
                "files": [{"name": n, "size": s, "checksum": c} for n, s, c in CandidateImporter._manifest(report)]}
        encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return "candidate-" + sha256(encoded).hexdigest()

    def _schema_and_state(self, connection: sqlite3.Connection, batch_id: str) -> str:
        try:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"schema_migrations", "import_batches", "streams", "stream_samples", "viewers", "vod_assets"}
            if not required <= tables:
                raise CandidateImportError("candidate_schema_invalid", "candidate")
            # Do not regard an arbitrary database with convenient table names
            # as an approved candidate.  Validate the migration history and
            # the actual CREATE TABLE shapes before opening a write transaction.
            applied = connection.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version").fetchall()
            expected_history = [(m.version, m.name, m.checksum) for m in MIGRATIONS]
            if [(r[0], r[1], r[2]) for r in applied] != expected_history:
                raise CandidateImportError("candidate_schema_invalid", "candidate")
            expected_sql = {}
            for migration in MIGRATIONS:
                for statement in migration.statements:
                    if statement.startswith("CREATE TABLE "):
                        name = statement.split(" ", 3)[2]
                        expected_sql[name] = statement
            for name in required:
                actual = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
                if actual is None or actual[0] != expected_sql.get(name):
                    raise CandidateImportError("candidate_schema_invalid", "candidate")
            existing = connection.execute("SELECT id FROM import_batches WHERE id=?", (batch_id,)).fetchone()
            if existing: return "matching"
            any_batch = connection.execute("SELECT 1 FROM import_batches LIMIT 1").fetchone()
            domain = any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in ("streams", "stream_samples", "viewers", "vod_assets"))
            if any_batch or domain: raise CandidateImportError("candidate_not_empty", "candidate")
            return "fresh"
        except CandidateImportError: raise
        except sqlite3.Error as exc: raise CandidateImportError("candidate_schema_invalid", "candidate") from exc

    def _documents(self, report: InspectionReport) -> tuple[dict[str, Any], Counter[str]]:
        by_name = {e.name: e for e in report.manifest}
        base = self._inspector._source_root
        deferred: Counter[str] = Counter()
        docs: dict[str, Any] = {}
        for name in ("config.json", "viewers.json", "history/stream_index.json"):
            if name not in by_name: continue
            try: docs[name] = _json((base / name).read_bytes())
            except (InspectionError, OSError): deferred[f"{name}:invalid"] += 1
        jsonl: dict[str, list[tuple[int, dict[str, Any] | None]]] = {}
        for name in sorted(n for n in by_name if _STREAM.fullmatch(n)):
            rows=[]
            try:
                with (base / name).open("rb") as fh:
                    line=0
                    while True:
                        raw=fh.readline(2*1024*1024+1)
                        if not raw: break
                        line += 1
                        if len(raw) > 2*1024*1024 or not raw.strip(): rows.append((line,None)); continue
                        try: rows.append((line,_json(raw)))
                        except InspectionError: rows.append((line,None))
            except OSError: deferred["jsonl:unreadable"] += 1
            jsonl[name]=rows
        docs["_jsonl"] = jsonl
        return docs, deferred

    def _parse(self, report: InspectionReport, batch_id: str) -> tuple[list[tuple], list[tuple], list[tuple], list[tuple], tuple[tuple[str,int,int,int,int],...], Counter[str], Counter[str]]:
        docs, deferred = self._documents(report)
        for entity, field, count in report.unknown_fields:
            deferred[f'unknown:{entity}:{field or "unclassified"}'] += count
        config = docs.get("config.json")
        if isinstance(config,dict):
            for key in ('rules','presets','prediction_presets','layout'):
                if isinstance(config.get(key),(list,dict)) and config[key]:
                    deferred[f'configuration:{key}'] += len(config[key])
        broadcaster = config.get("broadcaster_id") if isinstance(config, dict) else None
        if type(broadcaster) is not str or not broadcaster:
            raise CandidateImportError("invalid_broadcaster", "config")
        streams: list[tuple] = []; samples: list[tuple] = []; viewers: list[tuple] = []; vods: list[tuple] = []
        counts: dict[str, list[int]] = {k:[0,0,0,0] for k in ("streams","samples","viewers","vod_assets")}
        index = docs.get("history/stream_index.json")
        index = index if isinstance(index, dict) else {}
        parsed_streams: dict[str, dict[str, Any]] = {}
        for sid, record in index.items():
            counts["streams"][0] += 1
            if type(sid) is not str or not sid or not isinstance(record, dict): counts["streams"][3] += 1; deferred["streams:invalid"] += 1; continue
            started = _aware(record.get("start_time")); title=record.get("title")
            if started is None or type(title) is not str or not title:
                counts["streams"][3] += 1; deferred["streams:invalid"] += 1; continue
            parsed_streams[sid]=record
            streams.append((sid,broadcaster,title,record.get("game_name") if type(record.get("game_name")) is str else None,
                            record.get("thumbnail_url") if type(record.get("thumbnail_url")) is str else None,(),started,
                            _duration(record.get("duration")),_integer(record.get("max_viewers")),_finite(record.get("avg_viewers")),
                            _integer(record.get("follower_count")),_integer(record.get("log_count")), batch_id))
            counts["streams"][1] += 1
        # samples are intentionally only the safe timestamp/metrics subset.
        stream_tags: dict[str, tuple[str, ...]] = {}
        for name, rows in docs.get("_jsonl", {}).items():
            sid=name[len("history/stream_"):-len(".jsonl")]
            for line, item in rows:
                counts["samples"][0] += 1
                if isinstance(item,dict):
                    for key in ('messages','events','raids','points','census','emotes','subs','badges'):
                        if isinstance(item.get(key),(list,dict)) and item[key]:
                            deferred[f'legacy_activity:{key}'] += len(item[key])
                if sid not in parsed_streams or not isinstance(item, dict): counts["samples"][2] += 1; deferred["samples:invalid_or_orphan"] += 1; continue
                # Reuse the staged inspector's full bounded structural contract;
                # rejected raw activity never becomes an apparently valid sample.
                if not self._inspector._jsonl_shape(item, name, line, [], Counter()):
                    counts["samples"][3] += 1; deferred["samples:invalid_shape"] += 1; continue
                at=_aware(item.get("timestamp")); metrics=item.get("metrics"); info=item.get("stream_info")
                if at is None or not isinstance(metrics,dict) or not isinstance(info,dict):
                    reason='samples:invalid'
                    if at is None and type(item.get('timestamp')) is str:
                        try:
                            if datetime.fromisoformat(item['timestamp']).tzinfo is None: reason='samples:timezone_missing'
                        except ValueError: pass
                    counts["samples"][2]+=1; deferred[reason]+=1; continue
                vals=(_integer(metrics.get("viewer_count")),_integer(metrics.get("chat_count")),_finite(metrics.get("msg_speed")),_integer(metrics.get("bits")),_integer(metrics.get("gift_subs")),_integer(info.get("follower_total")))
                # A metric that is present but invalid must not be normalized.
                if any(key in metrics and value is None for key,value in zip(("viewer_count","chat_count","msg_speed","bits","gift_subs"), vals[:5])) or ("follower_total" in info and vals[5] is None):
                    counts["samples"][3]+=1; deferred["samples:invalid_metric"]+=1; continue
                samples.append((sid,at,*vals)); counts["samples"][1]+=1
                tags=info.get("tags")
                if isinstance(tags, list) and all(type(tag) is str for tag in tags):
                    stream_tags.setdefault(sid, tuple(tags))
        viewers_doc=docs.get("viewers.json")
        if isinstance(viewers_doc,dict):
            for uid, record in viewers_doc.items():
                counts["viewers"][0]+=1
                if type(uid) is not str or not uid or not isinstance(record,dict): counts["viewers"][3]+=1; deferred["viewers:invalid"]+=1; continue
                numeric=("total_visits","total_duration","total_comments","total_bits","total_sub_months","total_gifts_given","total_gifts_received","streak")
                nums=[_integer(record.get(k)) if k in record else None for k in numeric]
                if any(k in record and value is None for k,value in zip(numeric,nums)) or ("is_sub" in record and type(record["is_sub"]) is not bool):
                    counts["viewers"][3]+=1; deferred["viewers:invalid"]+=1; continue
                dates, day_only = _viewer_dates(record)
                if dates is None or ('is_follower' in record and type(record['is_follower']) is not bool):
                    counts["viewers"][3]+=1; deferred["viewers:invalid"]+=1; continue
                text=("login","name","last_sub_plan","last_stream_id","memo")
                values=[record.get(k) if k in record else None for k in text]
                if any(v is not None and type(v) is not str for v in values): counts["viewers"][3]+=1; deferred["viewers:invalid"]+=1; continue
                metadata={}
                if day_only:
                    metadata['date_only']=day_only
                    deferred['viewers:date_only_preserved']+=len(day_only)
                if 'is_follower' in record: metadata['is_follower']=record['is_follower']
                viewers.append((uid,values[0],values[1],dates[0],dates[1],nums[0],nums[1],nums[2],nums[3],record.get("is_sub"),nums[4],nums[5],nums[6],nums[7],dates[2],values[2],dates[3],values[3],values[4],json.dumps(metadata,sort_keys=True))); counts["viewers"][1]+=1
        for sid, record in parsed_streams.items():
            has=any(k in record and record[k] not in (None, "") for k in ("vod_id","vod_status","file_path"))
            if not has: continue
            code=self._inspector._vod(record.get("file_path")) if "file_path" in record else "absent"
            safe_path=None
            if code in {"safe_relative","absolute_inside"}:
                raw=record.get("file_path")
                candidate=Path(raw)
                safe_path=candidate.relative_to(self._inspector._downloads_root).as_posix() if candidate.is_absolute() else raw
            elif "file_path" in record: deferred[f"vod:{code}"]+=1
            status=record.get("vod_status")
            states={"downloaded":"present","not_downloaded":"missing","failed":"failed","downloading":"deferred"}
            local=states.get(status,"unknown") if type(status) is str else "unknown"
            vodid=record.get("vod_id") if type(record.get("vod_id")) is str and record.get("vod_id") else None
            vods.append(("vod-"+sha256(sid.encode()).hexdigest(),sid,vodid,safe_path,None,"known" if vodid else "unknown",local)); counts["vod_assets"][0]+=1; counts["vod_assets"][1]+=1
        # completeness requires samples; replace metadata-only flag now without raw state.
        sample_streams={s[0] for s in samples}
        streams=[(*row[:5], stream_tags.get(row[0], ()), row[6],row[7],row[8],row[9],row[10],row[11], "partial" if row[0] in sample_streams else "metadata_only",row[12]) for row in streams]
        return streams,samples,viewers,vods,tuple((e,*counts[e]) for e in ("streams","samples","viewers","vod_assets")),deferred,Counter(dict(report.vod_counts))

    @staticmethod
    def _aggregate(connection: sqlite3.Connection) -> tuple[tuple[str,int|float],...]:
        row=connection.execute("SELECT count(*) streams, coalesce(sum(max_viewers),0) max_sum, coalesce(sum(follower_count),0) followers, coalesce(sum(total_comments),0) comments FROM streams").fetchone()
        sample=connection.execute("SELECT count(*) samples, coalesce(sum(viewer_count),0) viewers, coalesce(sum(chat_count),0) chat, coalesce(sum(bits),0) bits, coalesce(sum(gift_subscriptions),0) gifts FROM stream_samples").fetchone()
        viewers=connection.execute("SELECT count(*) FROM viewers").fetchone()[0]; vods=connection.execute("SELECT count(*) FROM vod_assets").fetchone()[0]
        return tuple(sorted({"streams":row[0],"viewers":viewers,"samples":sample[0],"vod_assets":vods,"stream_max_viewers_sum":row[1],"stream_follower_sum":row[2],"stream_comments_sum":row[3],"sample_viewer_sum":sample[1],"sample_chat_sum":sample[2],"sample_bits_sum":sample[3],"sample_gifts_sum":sample[4]}.items()))

    @staticmethod
    def _expected_aggregate(streams: list[tuple], samples: list[tuple], viewers: list[tuple], vods: list[tuple]) -> tuple[tuple[str, int | float], ...]:
        # SQL SUM ignores NULL.  These safe mapped values therefore provide the
        # exact, representation-independent counterpart for verification.
        def total(rows, position): return sum(row[position] for row in rows if row[position] is not None)
        return tuple(sorted({
            "streams": len(streams), "viewers": len(viewers), "samples": len(samples), "vod_assets": len(vods),
            "stream_max_viewers_sum": total(streams, 8), "stream_follower_sum": total(streams, 10),
            "stream_comments_sum": total(streams, 11), "sample_viewer_sum": total(samples, 2),
            "sample_chat_sum": total(samples, 3), "sample_bits_sum": total(samples, 5),
            "sample_gifts_sum": total(samples, 6),
        }.items()))

    def _report(self, inspected: InspectionReport, result: str, counts, deferred, vod, aggregate) -> CandidateImportReport:
        return CandidateImportReport(inspected.source_reference,self._batch_id(inspected),inspected.cutoff,result,self._manifest(inspected),counts,aggregate,tuple(sorted(deferred.items())),tuple(sorted(vod.items())))

    def import_report(self, report: InspectionReport) -> CandidateImportReport:
        inspected=self._validate_report(report); batch_id=self._batch_id(inspected)
        streams,samples,viewers,vods,counts,deferred,vod_counts=self._parse(inspected,batch_id)
        try:
            with self._database.connection() as c:
                state=self._schema_and_state(c,batch_id)
                if state == "matching":
                    verified=self.verify_import(report)
                    return CandidateImportReport(verified.source_reference,verified.batch_id,verified.cutoff,"no_op",verified.manifest,verified.entity_counts,verified.aggregates,verified.deferred_counts,verified.vod_counts)
                c.execute("BEGIN IMMEDIATE")
                try:
                    manifest={"files":[{"name":n,"size":s,"checksum":h} for n,s,h in self._manifest(inspected)]}
                    now=to_rfc3339(self._clock())
                    c.execute("INSERT INTO import_batches VALUES (?,?,?,?,?,?,?,?)",(batch_id,_VERSION,now,inspected.cutoff,json.dumps(manifest,separators=(",",":"),sort_keys=True),inspected.source_reference,"completed",None))
                    for row in streams:
                        sid,channel,title,game,thumb,tags,started,duration,maxv,avg,followers,comments,complete,batch=row
                        c.execute("INSERT INTO streams(id,channel_id,title,game_name,thumbnail_url,tags_json,started_at,duration_seconds,source,completeness,max_viewers,average_viewers,follower_count,total_comments,legacy_metadata_json,import_batch_id,created_at,updated_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(sid,channel,title,game,thumb,json.dumps(list(tags),separators=(",",":")),to_rfc3339(started),duration,"imported",complete,maxv,avg,followers,comments,"{}",batch,now,now,1))
                    for sid,at,*vals in samples: c.execute("INSERT INTO stream_samples VALUES (?,?,?,?,?,?,?,?)",(sid,to_rfc3339(at),*vals))
                    for row in viewers:
                        c.execute(f"INSERT INTO viewers({_VIEWER_COLUMNS},created_at,updated_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",tuple(to_rfc3339(x) if isinstance(x,datetime) else x for x in row)+(now,now,1))
                    for row in vods: c.execute("INSERT INTO vod_assets VALUES (?,?,?,?,?,?,?,?,?,?)",(*row[:4],row[4],None,None,row[5],row[6],1))
                    aggregate=self._aggregate(c)
                    # This is deliberately the final operation before commit:
                    # even a source mutation during aggregate reads must undo
                    # every proposed candidate row.
                    self._inspector.verify_unchanged(report)
                    c.commit()
                except Exception:
                    c.rollback(); raise
        except CandidateImportError: raise
        except InspectionError as exc: raise CandidateImportError("source_changed" if exc.code == "source_changed" else exc.code,"source") from exc
        except (PersistenceError,sqlite3.Error,ValueError,TypeError) as exc: raise CandidateImportError("candidate_write_failed","candidate") from exc
        result=self._report(inspected,"completed",counts,deferred,vod_counts,aggregate)
        # Uses the same checks as future no-op detection.
        try: self.verify_import(report)
        except CandidateImportError as exc: raise CandidateImportError("candidate_verification_failed","candidate") from exc
        return result

    def verify_import(self, report: InspectionReport) -> CandidateImportReport:
        inspected=self._validate_report(report); batch_id=self._batch_id(inspected)
        streams,samples,viewers,vods,counts,deferred,vod_counts=self._parse(inspected,batch_id)
        try:
            with self._database.connection() as c:
                if self._schema_and_state(c,batch_id) != "matching": raise CandidateImportError("candidate_verification_failed","candidate")
                row=c.execute("SELECT importer_version,cutoff_at,source_manifest_json,source_base_reference,result,report_reference FROM import_batches WHERE id=?",(batch_id,)).fetchone()
                expected={"files":[{"name":n,"size":s,"checksum":h} for n,s,h in self._manifest(inspected)]}
                if row is None or row[0] != _VERSION or row[1] != inspected.cutoff or row[3] != inspected.source_reference or row[4] != "completed" or row[5] is not None or json.loads(row[2]) != expected: raise CandidateImportError("candidate_verification_failed","candidate")
                aggregate=self._aggregate(c)
                actual_viewers=[tuple(row) for row in c.execute(f'SELECT {_VIEWER_COLUMNS} FROM viewers ORDER BY user_id')]
                expected_viewers=sorted(tuple(to_rfc3339(x) if isinstance(x,datetime) else x for x in row) for row in viewers)
                if actual_viewers != expected_viewers:
                    raise CandidateImportError('candidate_verification_failed','candidate')
        except CandidateImportError: raise
        except (PersistenceError,sqlite3.Error,ValueError,TypeError) as exc: raise CandidateImportError("candidate_verification_failed","candidate") from exc
        if aggregate != self._expected_aggregate(streams, samples, viewers, vods):
            raise CandidateImportError("candidate_verification_failed","candidate")
        return self._report(inspected,"verified",counts,deferred,vod_counts,aggregate)
