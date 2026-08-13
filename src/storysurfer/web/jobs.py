"""Durable SQLite jobs and a single-machine cooperative worker."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from storysurfer.errors import JobCancelled, StorySurferError

JobState = Literal["queued", "running", "completed", "failed", "cancelled"]
JobHandler = Callable[
    [str, str, Callable[[str, int, str], None], Callable[[], None]], None
]


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    run_id: str
    kind: str
    idempotency_key: str
    state: JobState
    stage: str
    progress: int
    message: str
    error: str | None
    cancel_requested: bool
    created_at: str
    updated_at: str


class JobStore:
    def __init__(self, path: Path, *, now: Callable[[], str] | None = None) -> None:
        self.path = path
        self._now = now or _utc_now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue(self, run_id: str, kind: str, idempotency_key: str) -> JobRecord:
        now = self._now()
        job_id = uuid.uuid4().hex
        with self._connect() as database:
            database.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    id, run_id, kind, idempotency_key, state, stage, progress,
                    message, error, cancel_requested, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 'queued', 0, 'Waiting to start.', NULL, 0, ?, ?)
                """,
                (job_id, run_id, kind, idempotency_key, now, now),
            )
            row = database.execute(
                "SELECT * FROM jobs WHERE run_id = ? AND kind = ? AND idempotency_key = ?",
                (run_id, kind, idempotency_key),
            ).fetchone()
            if row is not None and row["state"] in {"failed", "cancelled"}:
                database.execute(
                    """
                    UPDATE jobs SET state = 'queued', stage = 'queued', progress = 0,
                        message = 'Waiting to retry.', error = NULL,
                        cancel_requested = 0, updated_at = ? WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                row = database.execute(
                    "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                ).fetchone()
        assert row is not None
        return _record(row)

    def claim_next(self) -> JobRecord | None:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                database.commit()
                return None
            now = self._now()
            database.execute(
                """
                UPDATE jobs SET state = 'running', stage = 'starting', progress = 1,
                    message = 'Starting job.', updated_at = ? WHERE id = ?
                """,
                (now, row["id"]),
            )
            database.commit()
        return self.get(cast(str, row["id"]))

    def update(
        self,
        job_id: str,
        *,
        state: JobState | None = None,
        stage: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> JobRecord:
        current = self.get(job_id)
        values = (
            state or current.state,
            stage if stage is not None else current.stage,
            max(0, min(100, progress if progress is not None else current.progress)),
            message if message is not None else current.message,
            error,
            self._now(),
            job_id,
        )
        with self._connect() as database:
            database.execute(
                """
                UPDATE jobs SET state = ?, stage = ?, progress = ?, message = ?,
                    error = ?, updated_at = ? WHERE id = ?
                """,
                values,
            )
        return self.get(job_id)

    def request_cancel(self, run_id: str) -> int:
        with self._connect() as database:
            cursor = database.execute(
                """
                UPDATE jobs SET cancel_requested = 1,
                    state = CASE WHEN state = 'queued' THEN 'cancelled' ELSE state END,
                    stage = CASE WHEN state = 'queued' THEN 'cancelled' ELSE stage END,
                    message = CASE WHEN state = 'queued' THEN 'Cancelled before start.'
                                   ELSE 'Cancellation requested.' END,
                    updated_at = ?
                WHERE run_id = ? AND state IN ('queued', 'running')
                """,
                (self._now(), run_id),
            )
            return cursor.rowcount

    def delete_for_run(self, run_id: str) -> int:
        """Delete inactive job history for a run while atomically rejecting active work."""
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            active = database.execute(
                "SELECT 1 FROM jobs WHERE run_id = ? AND state = 'running' LIMIT 1",
                (run_id,),
            ).fetchone()
            if active is not None:
                database.rollback()
                raise JobCancelled(
                    "The project still has a running job. Cancellation was requested; "
                    "wait for it to stop, then delete again."
                )
            cursor = database.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))
            database.commit()
            return cursor.rowcount

    def cancellation_requested(self, job_id: str) -> bool:
        return self.get(job_id).cancel_requested

    def recover_interrupted(self) -> int:
        with self._connect() as database:
            cursor = database.execute(
                """
                UPDATE jobs SET state = 'queued', stage = 'recovered', progress = 0,
                    message = 'Recovered after server restart.', updated_at = ?
                WHERE state = 'running' AND cancel_requested = 0
                """,
                (self._now(),),
            )
            database.execute(
                """
                UPDATE jobs SET state = 'cancelled', stage = 'cancelled',
                    message = 'Cancelled during server restart.', updated_at = ?
                WHERE state = 'running' AND cancel_requested = 1
                """,
                (self._now(),),
            )
            return cursor.rowcount

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as database:
            row = database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _record(row)

    def latest_for_run(self, run_id: str) -> JobRecord | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM jobs WHERE run_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return _record(row) if row is not None else None

    def list_for_run(self, run_id: str) -> tuple[JobRecord, ...]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT * FROM jobs WHERE run_id = ? ORDER BY created_at DESC, id DESC",
                (run_id,),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        return database

    def _initialize(self) -> None:
        with self._connect() as database:
            database.execute("PRAGMA journal_mode = WAL")
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, kind, idempotency_key)
                )
                """
            )


class JobWorker:
    def __init__(
        self,
        store: JobStore,
        handler: JobHandler,
        *,
        poll_seconds: float = 0.25,
    ) -> None:
        self.store = store
        self.handler = handler
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.store.recover_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="storysurfer-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> bool:
        job = self.store.claim_next()
        if job is None:
            return False

        def progress(stage: str, percentage: int, message: str) -> None:
            self.store.update(
                job.id,
                state="running",
                stage=stage,
                progress=percentage,
                message=message,
            )

        def check_cancelled() -> None:
            if self.store.cancellation_requested(job.id):
                raise JobCancelled("Job cancellation was requested.")

        try:
            check_cancelled()
            self.handler(job.kind, job.run_id, progress, check_cancelled)
            check_cancelled()
            self.store.update(
                job.id,
                state="completed",
                stage="completed",
                progress=100,
                message="Job completed.",
            )
        except JobCancelled as exc:
            self.store.update(
                job.id,
                state="cancelled",
                stage="cancelled",
                message=exc.message,
                error=None,
            )
        except StorySurferError as exc:
            self.store.update(
                job.id,
                state="failed",
                stage="failed",
                message=exc.message,
                error=exc.display(),
            )
        except Exception:
            self.store.update(
                job.id,
                state="failed",
                stage="failed",
                message="Unexpected job failure.",
                error="Unexpected job failure.",
            )
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._wake.wait(self.poll_seconds)
                self._wake.clear()


def _record(row: sqlite3.Row) -> JobRecord:
    state = cast(JobState, row["state"])
    return JobRecord(
        id=cast(str, row["id"]),
        run_id=cast(str, row["run_id"]),
        kind=cast(str, row["kind"]),
        idempotency_key=cast(str, row["idempotency_key"]),
        state=state,
        stage=cast(str, row["stage"]),
        progress=cast(int, row["progress"]),
        message=cast(str, row["message"]),
        error=cast(str | None, row["error"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=cast(str, row["created_at"]),
        updated_at=cast(str, row["updated_at"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
