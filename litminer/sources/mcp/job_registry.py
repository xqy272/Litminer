"""In-memory and persisted MCP background-job registry."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from litminer.engine.common import write_text_atomic
from litminer.runtime.state_store import StateStore, default_state_store_path


class JobRegistry:
    def __init__(
        self,
        *,
        workspace_root: Callable[[], Path],
        runtime_store: Callable[[], StateStore],
    ) -> None:
        self._workspace_root = workspace_root
        self._runtime_store = runtime_store
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def safe_job_id(job_id: str) -> str:
        if not job_id or not all(
            character.isalnum() or character in "-_"
            for character in job_id
        ):
            raise ValueError(f"invalid Litminer job_id: {job_id!r}")
        return job_id

    def record_path(self, job_id: str) -> Path:
        return (
            self._workspace_root()
            / ".litminer"
            / "jobs"
            / f"{self.safe_job_id(job_id)}.json"
        )

    @staticmethod
    def public_record(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key not in {"cancel_event", "thread"}
            and not key.startswith("_")
        }

    def _store_for(self, record: dict[str, Any]) -> StateStore:
        state_path = str(record.get("state_store") or "")
        state_enabled = bool(record.get("state_enabled", True))
        return StateStore(
            state_path
            or default_state_store_path(self._workspace_root()),
            enabled=state_enabled,
        )

    def persist_unlocked(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            return
        public = self.public_record(job)
        write_text_atomic(
            self.record_path(job_id),
            json.dumps(public, indent=2, ensure_ascii=False) + "\n",
        )
        self._store_for(public).upsert_job(public)

    def create(self, job: dict[str, Any]) -> None:
        job_id = self.safe_job_id(str(job.get("job_id") or ""))
        with self.lock:
            self.jobs[job_id] = job
            self.persist_unlocked(job)

    def update(self, job_id: str, **fields: Any) -> None:
        job_id = self.safe_job_id(job_id)
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError(f"unknown Litminer job_id: {job_id}")
            self.jobs[job_id].update(fields)
            self.persist_unlocked(self.jobs[job_id])

    def attach_thread(
        self,
        job_id: str,
        thread: threading.Thread,
    ) -> None:
        job_id = self.safe_job_id(job_id)
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError(f"unknown Litminer job_id: {job_id}")
            self.jobs[job_id]["thread"] = thread

    def load_persisted(self, job_id: str) -> dict[str, Any]:
        job_id = self.safe_job_id(job_id)
        path = self.record_path(job_id)
        if not path.exists():
            data = self._runtime_store().get_job(job_id)
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = self._runtime_store().get_job(job_id)
        if not isinstance(data, dict):
            return {}
        if data.get("status") in {"queued", "running", "cancelling"}:
            data = {
                **data,
                "status": "interrupted",
                "note": (
                    "This job record was loaded from disk, but no live MCP "
                    "worker owns it."
                ),
            }
            self.persist_unlocked(data)
        return data

    def snapshot(self, job_id: str) -> dict[str, Any]:
        job_id = self.safe_job_id(str(job_id or ""))
        with self.lock:
            job = self.public_record(self.jobs.get(job_id) or {})
        if not job:
            job = self.load_persisted(job_id)
        if not job:
            raise ValueError(f"unknown Litminer job_id: {job_id}")
        return job

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        job_id = self.safe_job_id(str(job_id or ""))
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError(f"unknown Litminer job_id: {job_id}")
            cancel_event = self.jobs[job_id].get("cancel_event")
            if isinstance(cancel_event, threading.Event):
                cancel_event.set()
            self.jobs[job_id]["cancel_requested"] = True
            if self.jobs[job_id].get("status") in {"queued", "running"}:
                self.jobs[job_id]["status"] = "cancelling"
            self.persist_unlocked(self.jobs[job_id])
        return {
            "status": "cancel_requested",
            "job_id": job_id,
            "note": "Engine will stop at the next stage boundary.",
        }
