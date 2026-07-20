"""Workspace-local SQLite state, request ledger, and evidence ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


STATE_ENV = "LITMINER_STATE_STORE"
DEFAULT_STATE_PATH = ".litminer/state/litminer.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_state_store_path(workspace_root: Path | str | None = None) -> Path:
    configured = os.environ.get(STATE_ENV)
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        return Path(workspace_root or Path.cwd()) / path
    return Path(workspace_root or Path.cwd()) / DEFAULT_STATE_PATH


def query_fingerprint(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS research_sessions (
            session_id TEXT PRIMARY KEY,
            workspace_root TEXT NOT NULL,
            output_dir TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS iterations (
            iteration_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE,
            input_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            quality TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (session_id, iteration_id),
            FOREIGN KEY (session_id) REFERENCES research_sessions(session_id)
        );
        CREATE TABLE IF NOT EXISTS stage_runs (
            run_id TEXT NOT NULL,
            stage_name TEXT NOT NULL,
            status TEXT NOT NULL,
            status_class TEXT NOT NULL,
            input_path TEXT NOT NULL DEFAULT '',
            output_path TEXT NOT NULL DEFAULT '',
            input_count INTEGER NOT NULL DEFAULT 0,
            output_count INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '',
            error_json TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, stage_name)
        );
        CREATE TABLE IF NOT EXISTS provider_health (
            provider TEXT PRIMARY KEY,
            last_success_at TEXT NOT NULL DEFAULT '',
            last_failure_at TEXT NOT NULL DEFAULT '',
            last_status_class TEXT NOT NULL DEFAULT '',
            failure_streak INTEGER NOT NULL DEFAULT 0,
            not_before TEXT NOT NULL DEFAULT '',
            last_retry_after_seconds REAL,
            credential_state TEXT NOT NULL DEFAULT 'unknown',
            tls_state TEXT NOT NULL DEFAULT 'unknown',
            recent_latency_ms REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_requests (
            request_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL DEFAULT '',
            iteration_id TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL,
            operation TEXT NOT NULL,
            query_hash TEXT NOT NULL DEFAULT '',
            attempt INTEGER NOT NULL DEFAULT 1,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            latency_ms REAL,
            http_status INTEGER,
            status_class TEXT NOT NULL,
            retry_after_seconds REAL,
            response_bytes INTEGER,
            cache_status TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            wait_seconds REAL NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_provider_requests_run ON provider_requests(run_id, provider);
        CREATE INDEX IF NOT EXISTS idx_provider_requests_provider_time ON provider_requests(provider, started_at);
        CREATE TABLE IF NOT EXISTS source_observations (
            observation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL DEFAULT '',
            iteration_id TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL,
            operation TEXT NOT NULL DEFAULT '',
            query_id TEXT NOT NULL DEFAULT '',
            retrieved_at TEXT NOT NULL,
            paper_hint TEXT NOT NULL DEFAULT '',
            raw_identifier TEXT NOT NULL DEFAULT '',
            raw_title TEXT NOT NULL DEFAULT '',
            raw_year TEXT NOT NULL DEFAULT '',
            raw_payload_hash TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_observations_run ON source_observations(run_id);
        CREATE INDEX IF NOT EXISTS idx_observations_hint ON source_observations(paper_hint);
        CREATE TABLE IF NOT EXISTS paper_records (
            paper_id TEXT PRIMARY KEY,
            canonical_json TEXT NOT NULL,
            bibliographic_status TEXT NOT NULL DEFAULT '',
            retraction_status TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_identifiers (
            paper_id TEXT NOT NULL,
            identifier_type TEXT NOT NULL,
            identifier_value TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (identifier_type, identifier_value),
            FOREIGN KEY (paper_id) REFERENCES paper_records(paper_id)
        );
        CREATE TABLE IF NOT EXISTS field_values (
            paper_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_value TEXT NOT NULL,
            source TEXT NOT NULL,
            trust_class TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (paper_id, field_name, source, field_value),
            FOREIGN KEY (paper_id) REFERENCES paper_records(paper_id)
        );
        CREATE TABLE IF NOT EXISTS artifact_snapshots (
            run_id TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL DEFAULT '',
            schema_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, name)
        );
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS run_outcomes (
            run_id TEXT PRIMARY KEY,
            output_dir TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            quality TEXT NOT NULL,
            outcome_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS runtime_events (
            event_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_events_entity
            ON runtime_events(entity_type, entity_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_events_run
            ON runtime_events(run_id, created_at);
        """,
    ),
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1][0]


class StateStore:
    """Small SQLite repository; each operation owns its connection."""

    def __init__(self, path: Path | str, *, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)
        if self.enabled:
            self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.enabled:
            raise RuntimeError("Litminer state store is disabled")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.commit()
            applied = {
                int(row[0])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                applied_at = utc_now().replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + sql
                    + f"\nINSERT INTO schema_migrations(version, applied_at) "
                      f"VALUES ({int(version)}, '{applied_at}');\nCOMMIT;"
                )
                try:
                    connection.executescript(script)
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
        finally:
            connection.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _record_event_db(
        self,
        db: sqlite3.Connection,
        *,
        entity_type: str,
        entity_id: str,
        event_type: str,
        status: str = "",
        run_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO runtime_events(
                event_id, entity_type, entity_id, run_id, event_type,
                status, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                entity_type,
                entity_id,
                run_id,
                event_type,
                status,
                self._json(payload or {}),
                utc_now(),
            ),
        )
        return event_id

    def record_event(
        self,
        *,
        entity_type: str,
        entity_id: str,
        event_type: str,
        status: str = "",
        run_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> str:
        if not self.enabled:
            return ""
        with self.connect() as db:
            return self._record_event_db(
                db,
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                status=status,
                run_id=run_id,
                payload=payload,
            )

    def list_events(
        self,
        *,
        entity_type: str = "",
        entity_id: str = "",
        run_id: str = "",
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        clauses: list[str] = []
        values: list[str] = []
        for column, value in (
            ("entity_type", entity_type),
            ("entity_id", entity_id),
            ("run_id", run_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM runtime_events"
                + where
                + " ORDER BY rowid",
                values,
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            events.append(item)
        return events

    def upsert_session(self, session_id: str, *, workspace_root: str, output_dir: str) -> None:
        if not self.enabled:
            return
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO research_sessions(session_id, workspace_root, output_dir, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace_root=excluded.workspace_root,
                    output_dir=excluded.output_dir,
                    updated_at=excluded.updated_at
                """,
                (session_id, workspace_root, output_dir, now, now),
            )
            self._record_event_db(
                db,
                entity_type="session",
                entity_id=session_id,
                event_type="session_upserted",
                status="active",
                payload={"workspace_root": workspace_root, "output_dir": output_dir},
            )

    def start_iteration(
        self,
        *,
        session_id: str,
        iteration_id: str,
        run_id: str,
        input_mode: str,
        spec: dict[str, Any],
        status: str = "running",
        quality: str = "inconclusive",
    ) -> None:
        if not self.enabled:
            return
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO iterations(
                    iteration_id, session_id, run_id, input_mode, status, quality,
                    spec_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    quality=excluded.quality,
                    spec_json=excluded.spec_json
                """,
                (iteration_id, session_id, run_id, input_mode, status, quality, self._json(spec), now),
            )
            self._record_event_db(
                db,
                entity_type="run",
                entity_id=run_id,
                run_id=run_id,
                event_type="iteration_started",
                status=status,
                payload={
                    "session_id": session_id,
                    "iteration_id": iteration_id,
                    "input_mode": input_mode,
                    "quality": quality,
                },
            )

    def complete_iteration(self, run_id: str, *, status: str, quality: str) -> None:
        if not self.enabled:
            return
        with self.connect() as db:
            db.execute(
                "UPDATE iterations SET status=?, quality=?, completed_at=? WHERE run_id=?",
                (status, quality, utc_now(), run_id),
            )
            self._record_event_db(
                db,
                entity_type="run",
                entity_id=run_id,
                run_id=run_id,
                event_type="iteration_completed",
                status=status,
                payload={"quality": quality},
            )

    def record_stage(
        self,
        *,
        run_id: str,
        stage_name: str,
        status: str,
        status_class: str,
        input_path: str = "",
        output_path: str = "",
        input_count: int = 0,
        output_count: int = 0,
        message: str = "",
        error: dict[str, Any] | None = None,
        started_at: str = "",
        completed_at: str = "",
    ) -> None:
        if not self.enabled:
            return
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO stage_runs(
                    run_id, stage_name, status, status_class, input_path, output_path,
                    input_count, output_count, message, error_json, started_at,
                    completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage_name) DO UPDATE SET
                    status=excluded.status,
                    status_class=excluded.status_class,
                    input_path=excluded.input_path,
                    output_path=excluded.output_path,
                    input_count=excluded.input_count,
                    output_count=excluded.output_count,
                    message=excluded.message,
                    error_json=excluded.error_json,
                    started_at=CASE WHEN excluded.started_at='' THEN stage_runs.started_at ELSE excluded.started_at END,
                    completed_at=excluded.completed_at,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id, stage_name, status, status_class, input_path, output_path,
                    int(input_count), int(output_count), message, self._json(error or {}),
                    started_at, completed_at, utc_now(),
                ),
            )
            self._record_event_db(
                db,
                entity_type="stage",
                entity_id=f"{run_id}:{stage_name}",
                run_id=run_id,
                event_type="stage_recorded",
                status=status,
                payload={
                    "stage_name": stage_name,
                    "status_class": status_class,
                    "input_count": int(input_count),
                    "output_count": int(output_count),
                    "message": message,
                },
            )

    def get_provider_health(self, provider: str) -> dict[str, Any]:
        if not self.enabled:
            return {}
        with self.connect() as db:
            row = db.execute("SELECT * FROM provider_health WHERE provider=?", (provider,)).fetchone()
        if row is None:
            return {}
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            data["metadata"] = {}
        return data

    def update_provider_health(self, provider: str, **fields: Any) -> None:
        if not self.enabled:
            return
        current = self.get_provider_health(provider)
        values = {
            "last_success_at": current.get("last_success_at", ""),
            "last_failure_at": current.get("last_failure_at", ""),
            "last_status_class": current.get("last_status_class", ""),
            "failure_streak": current.get("failure_streak", 0),
            "not_before": current.get("not_before", ""),
            "last_retry_after_seconds": current.get("last_retry_after_seconds"),
            "credential_state": current.get("credential_state", "unknown"),
            "tls_state": current.get("tls_state", "unknown"),
            "recent_latency_ms": current.get("recent_latency_ms"),
            "metadata": current.get("metadata", {}),
        }
        values.update(fields)
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO provider_health(
                    provider, last_success_at, last_failure_at, last_status_class,
                    failure_streak, not_before, last_retry_after_seconds,
                    credential_state, tls_state, recent_latency_ms, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    last_success_at=excluded.last_success_at,
                    last_failure_at=excluded.last_failure_at,
                    last_status_class=excluded.last_status_class,
                    failure_streak=excluded.failure_streak,
                    not_before=excluded.not_before,
                    last_retry_after_seconds=excluded.last_retry_after_seconds,
                    credential_state=excluded.credential_state,
                    tls_state=excluded.tls_state,
                    recent_latency_ms=excluded.recent_latency_ms,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    provider,
                    values["last_success_at"], values["last_failure_at"], values["last_status_class"],
                    int(values["failure_streak"] or 0), values["not_before"], values["last_retry_after_seconds"],
                    values["credential_state"], values["tls_state"], values["recent_latency_ms"],
                    self._json(values["metadata"] or {}), utc_now(),
                ),
            )

    def record_provider_request(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self.connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO provider_requests(
                    request_id, run_id, iteration_id, provider, operation, query_hash,
                    attempt, started_at, ended_at, latency_ms, http_status, status_class,
                    retry_after_seconds, response_bytes, cache_status, error_code,
                    wait_seconds, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["request_id"], record.get("run_id", ""), record.get("iteration_id", ""),
                    record.get("provider", ""), record.get("operation", ""), record.get("query_hash", ""),
                    int(record.get("attempt", 1)), record.get("started_at", utc_now()), record.get("ended_at", utc_now()),
                    record.get("latency_ms"), record.get("http_status"), record.get("status_class", "unknown"),
                    record.get("retry_after_seconds"), record.get("response_bytes"), record.get("cache_status", ""),
                    record.get("error_code", ""), float(record.get("wait_seconds", 0) or 0),
                    self._json(record.get("metadata", {})),
                ),
            )

    def request_summary(self, run_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "requests": 0}
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT provider, status_class, COUNT(*) AS requests,
                       COALESCE(SUM(wait_seconds), 0) AS wait_seconds,
                       COALESCE(SUM(CASE WHEN attempt > 1 THEN 1 ELSE 0 END), 0) AS retries
                FROM provider_requests WHERE run_id=?
                GROUP BY provider, status_class ORDER BY provider, status_class
                """,
                (run_id,),
            ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "enabled": True,
            "requests": sum(int(item["requests"]) for item in items),
            "wait_seconds": sum(float(item["wait_seconds"] or 0) for item in items),
            "retries": sum(int(item["retries"] or 0) for item in items),
            "by_provider_status": items,
        }

    def record_observation(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        raw = record.get("raw", {})
        raw_json = self._json(raw)
        with self.connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO source_observations(
                    observation_id, run_id, iteration_id, provider, operation,
                    query_id, retrieved_at, paper_hint, raw_identifier, raw_title,
                    raw_year, raw_payload_hash, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["observation_id"], record.get("run_id", ""), record.get("iteration_id", ""),
                    record.get("provider", "unknown"), record.get("operation", "discovery"),
                    record.get("query_id", ""), record.get("retrieved_at", utc_now()),
                    record.get("paper_hint", ""), record.get("raw_identifier", ""),
                    record.get("raw_title", ""), record.get("raw_year", ""),
                    hashlib.sha256(raw_json.encode("utf-8")).hexdigest(), raw_json,
                ),
            )

    def upsert_canonical_paper(
        self,
        paper: dict[str, Any],
        *,
        identifiers: Iterable[tuple[str, str, str]] = (),
        field_values: Iterable[dict[str, Any]] = (),
    ) -> None:
        if not self.enabled:
            return
        paper_id = str(paper["paper_id"])
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO paper_records(paper_id, canonical_json, bibliographic_status, retraction_status, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    canonical_json=excluded.canonical_json,
                    bibliographic_status=excluded.bibliographic_status,
                    retraction_status=excluded.retraction_status,
                    updated_at=excluded.updated_at
                """,
                (
                    paper_id, self._json(paper), paper.get("bibliographic_status", ""),
                    paper.get("retraction_status", ""), now,
                ),
            )
            for kind, value, source in identifiers:
                if value:
                    db.execute(
                        "INSERT OR REPLACE INTO paper_identifiers(paper_id, identifier_type, identifier_value, source) VALUES (?, ?, ?, ?)",
                        (paper_id, kind, value, source),
                    )
            for item in field_values:
                db.execute(
                    """
                    INSERT OR REPLACE INTO field_values(
                        paper_id, field_name, field_value, source, trust_class,
                        selected, reason, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_id, item.get("field_name", ""), item.get("field_value", ""),
                        item.get("source", ""), item.get("trust_class", ""),
                        1 if item.get("selected") else 0, item.get("reason", ""), now,
                    ),
                )

    def record_artifact(self, *, run_id: str, name: str, path: str, sha256: str = "", schema_version: int = 1) -> None:
        if not self.enabled:
            return
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO artifact_snapshots(run_id, name, path, sha256, schema_version, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, name, path, sha256, int(schema_version), utc_now()),
            )

    def upsert_job(self, job: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO jobs(job_id, run_id, status, payload_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    job.get("job_id", ""), job.get("run_id", ""), job.get("status", "unknown"),
                    self._json(job), utc_now(),
                ),
            )
            job_id = str(job.get("job_id", ""))
            job_status = str(job.get("status", "unknown"))
            self._record_event_db(
                db,
                entity_type="job",
                entity_id=job_id,
                run_id=str(job.get("run_id", "")),
                event_type=f"job_{job_status}",
                status=job_status,
                payload={
                    "quality": job.get("quality", "inconclusive"),
                    "output_dir": job.get("output_dir", ""),
                    "cancel_requested": bool(job.get("cancel_requested", False)),
                },
            )

    def get_job(self, job_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {}
        with self.connect() as db:
            row = db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def record_outcome(self, outcome: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self.connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO run_outcomes(run_id, output_dir, status, quality, outcome_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.get("run_id", ""), outcome.get("output_dir", ""), outcome.get("status", "unknown"),
                    outcome.get("quality", "inconclusive"), self._json(outcome), utc_now(),
                ),
            )
            run_id = str(outcome.get("run_id", ""))
            outcome_status = str(outcome.get("status", "unknown"))
            self._record_event_db(
                db,
                entity_type="run",
                entity_id=run_id,
                run_id=run_id,
                event_type="outcome_recorded",
                status=outcome_status,
                payload={
                    "quality": outcome.get("quality", "inconclusive"),
                    "output_dir": outcome.get("output_dir", ""),
                },
            )

    def get_run(self, *, run_id: str = "", output_dir: str = "") -> dict[str, Any]:
        if not self.enabled:
            return {}
        with self.connect() as db:
            if run_id:
                row = db.execute("SELECT outcome_json FROM run_outcomes WHERE run_id=?", (run_id,)).fetchone()
            else:
                row = db.execute(
                    "SELECT outcome_json FROM run_outcomes WHERE output_dir=? ORDER BY updated_at DESC LIMIT 1",
                    (output_dir,),
                ).fetchone()
            if row is not None:
                try:
                    value = json.loads(row[0])
                except json.JSONDecodeError:
                    value = {}
                if isinstance(value, dict):
                    return value

            if run_id:
                iteration = db.execute(
                    """
                    SELECT i.*, s.output_dir FROM iterations i
                    JOIN research_sessions s ON s.session_id=i.session_id
                    WHERE i.run_id=? LIMIT 1
                    """,
                    (run_id,),
                ).fetchone()
            else:
                iteration = db.execute(
                    """
                    SELECT i.*, s.output_dir FROM iterations i
                    JOIN research_sessions s ON s.session_id=i.session_id
                    WHERE s.output_dir=? ORDER BY i.created_at DESC LIMIT 1
                    """,
                    (output_dir,),
                ).fetchone()
            if iteration is None:
                if run_id:
                    job_only = db.execute(
                        "SELECT payload_json FROM jobs WHERE run_id=? ORDER BY updated_at DESC LIMIT 1",
                        (run_id,),
                    ).fetchone()
                    if job_only is not None:
                        try:
                            payload = json.loads(job_only[0])
                        except json.JSONDecodeError:
                            payload = {}
                        if isinstance(payload, dict):
                            return payload
                return {}
            resolved_run_id = str(iteration["run_id"])
            stages = [dict(item) for item in db.execute(
                "SELECT * FROM stage_runs WHERE run_id=? ORDER BY updated_at, stage_name",
                (resolved_run_id,),
            )]
            artifacts = {
                str(item["name"]): str(item["path"])
                for item in db.execute(
                    "SELECT name, path FROM artifact_snapshots WHERE run_id=? ORDER BY name",
                    (resolved_run_id,),
                )
            }
            job = db.execute(
                "SELECT job_id, status FROM jobs WHERE run_id=? ORDER BY updated_at DESC LIMIT 1",
                (resolved_run_id,),
            ).fetchone()
        try:
            spec = json.loads(str(iteration["spec_json"] or "{}"))
        except json.JSONDecodeError:
            spec = {}
        return {
            "ok": str(iteration["status"]) != "failed",
            "run_id": resolved_run_id,
            "status": str(iteration["status"]),
            "quality": str(iteration["quality"] or "inconclusive"),
            "output_dir": str(iteration["output_dir"] or ""),
            "iteration_id": str(iteration["iteration_id"]),
            "session_id": str(iteration["session_id"]),
            "run_spec": spec if isinstance(spec, dict) else {},
            "stages": stages,
            "artifacts": artifacts,
            "job_id": str(job["job_id"]) if job is not None else "",
            "state_source": "sqlite_runtime",
        }

    def export_state(self, output_path: Path) -> Path:
        if not self.enabled:
            output_path.write_text("{}\n", encoding="utf-8")
            return output_path
        tables = [
            "schema_migrations", "research_sessions", "iterations", "stage_runs",
            "provider_health", "provider_requests", "source_observations",
            "paper_records", "paper_identifiers", "field_values",
            "artifact_snapshots", "jobs", "run_outcomes", "runtime_events",
        ]
        payload: dict[str, Any] = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "generated_at": utc_now(),
            "tables": {},
        }
        with self.connect() as db:
            for table in tables:
                payload["tables"][table] = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Litminer SQLite runtime state as a portable JSON snapshot.")
    parser.add_argument("--state-store", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    store = StateStore(args.state_store or default_state_store_path())
    output = store.export_state(args.output)
    print(output)


if __name__ == "__main__":
    main()
