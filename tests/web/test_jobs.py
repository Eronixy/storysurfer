from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from storysurfer.config import AppConfig, StorageConfig, WebConfig
from storysurfer.errors import JobCancelled
from storysurfer.media.probe import MediaInfo
from storysurfer.storage import RunStorage
from storysurfer.web.jobs import JobStore, JobWorker
from storysurfer.web.models import ProjectSettings
from storysurfer.web.service import PipelineJobExecutor, ProjectRepository


def test_enqueue_is_idempotent_and_worker_runs_paid_stage_once(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    calls: list[tuple[str, str]] = []

    def handler(kind: str, run_id: str, progress: object, cancel: object) -> None:
        del progress, cancel
        calls.append((kind, run_id))

    first = store.enqueue("run-one", "preview", "same-input-hash")
    second = store.enqueue("run-one", "preview", "same-input-hash")
    worker = JobWorker(store, handler)

    assert first.id == second.id
    assert worker.run_once()
    assert not worker.run_once()
    assert calls == [("preview", "run-one")]
    assert store.get(first.id).state == "completed"


def test_interrupted_jobs_are_recovered_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    original = JobStore(database)
    queued = original.enqueue("recoverable-run", "prepare", "input-hash")
    claimed = original.claim_next()

    assert claimed is not None
    assert claimed.id == queued.id
    assert claimed.state == "running"

    restarted = JobStore(database)
    assert restarted.recover_interrupted() == 1
    recovered = restarted.get(queued.id)
    assert recovered.state == "queued"
    assert recovered.stage == "recovered"
    assert recovered.message == "Recovered after server restart."


def test_queued_job_can_be_cancelled_without_running(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.enqueue("cancel-run", "final", "input-hash")

    assert store.request_cancel("cancel-run") == 1
    cancelled = store.get(job.id)
    assert cancelled.state == "cancelled"
    assert cancelled.cancel_requested


def test_job_history_deletion_rejects_running_work(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    queued = store.enqueue("active-run", "preview", "input-hash")
    assert store.claim_next() is not None
    store.request_cancel("active-run")

    with pytest.raises(JobCancelled, match="still has a running job"):
        store.delete_for_run("active-run")

    store.update(queued.id, state="cancelled", stage="cancelled")
    assert store.delete_for_run("active-run") == 1
    assert store.list_for_run("active-run") == ()


def test_prepare_promotes_staged_upload_before_slow_pipeline_work(
    app_config: AppConfig, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    config = AppConfig(
        reddit=app_config.reddit,
        selection=app_config.selection,
        storage=StorageConfig(tmp_path / "runs"),
        media=app_config.media,
        speech=app_config.speech,
        captions=app_config.captions,
        web=WebConfig(database_path=tmp_path / "jobs.sqlite3"),
    )
    storage = RunStorage(config.storage.runs_dir)
    run_id = storage.create_run(config.public_dict())
    project = ProjectSettings(
        source_url="abc123",
        staging_path="staging/gameplay.mp4",
        background_path="assets/gameplay.mp4",
        preset="minecraft",
        target_duration_seconds=60,
        voice=config.speech.voice,
        update_urls=("https://www.reddit.com/comments/update1/",),
    )
    projects = ProjectRepository(storage)
    projects.write(run_id, project)
    staged = storage.internal_path(run_id, project.staging_path, create_parent=True)
    staged.write_bytes(b"synthetic gameplay")
    monkeypatch.setattr(
        "storysurfer.web.service.probe_media",
        lambda path, ffprobe: MediaInfo(path, 10_000, 1280, 720, False),
    )
    ingest_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "storysurfer.web.service.ingest_into_run",
        lambda *args, **kwargs: ingest_calls.append((*args, kwargs)),
    )
    monkeypatch.setattr("storysurfer.web.service.select", lambda *a, **k: None)
    monkeypatch.setattr("storysurfer.web.service.script_run", lambda *a, **k: None)

    PipelineJobExecutor(config, storage, projects)(
        "prepare", run_id, lambda *args: None, lambda: None
    )

    assert projects.background(run_id, project).read_bytes() == b"synthetic gameplay"
    assert not staged.exists()
    assert ingest_calls[0][4] == project.update_urls


def test_failed_job_can_retry_same_inputs_without_creating_a_duplicate(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    failed = store.enqueue("retry-run", "preview", "same-input-hash")
    store.update(
        failed.id,
        state="failed",
        stage="failed",
        message="Temporary failure.",
        error="Temporary failure.",
    )

    retried = store.enqueue("retry-run", "preview", "same-input-hash")

    assert retried.id == failed.id
    assert retried.state == "queued"
    assert retried.error is None
    assert not retried.cancel_requested
    assert len(store.list_for_run("retry-run")) == 1


def test_project_settings_preserve_updates_and_candidate_counts(
    app_config: AppConfig,
) -> None:
    legacy = ProjectSettings.from_dict(
        {
            "schema_version": 1,
            "source_url": "https://www.reddit.com/comments/main/",
            "staging_path": "staging/gameplay.mp4",
            "background_path": "assets/gameplay.mp4",
            "preset": "minecraft",
            "target_duration_seconds": 75,
            "voice": "fil-PH-AngeloNeural",
        }
    )
    updated = ProjectSettings.from_dict(
        {
            **legacy.to_dict(),
            "update_urls": ["https://www.reddit.com/comments/update/"],
            "requested_op_exchanges": 4,
            "requested_comments": 6,
        }
    )

    assert legacy.update_urls == ()
    assert legacy.requested_op_exchanges == 5
    assert legacy.requested_comments == 5
    assert updated.update_urls == ("https://www.reddit.com/comments/update/",)
    assert updated.requested_op_exchanges == 4
    assert updated.requested_comments == 6
    applied = updated.apply(app_config)
    assert applied.selection.requested_op_exchanges == 4
    assert applied.selection.requested_comments == 6
