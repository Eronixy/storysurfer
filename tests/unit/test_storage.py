from __future__ import annotations

from pathlib import Path

import pytest

from redditsurfer.domain import ThreadSnapshot
from redditsurfer.errors import StorageError
from redditsurfer.storage import RunStorage


def test_run_artifacts_are_atomic_and_hashed(
    tmp_path: Path, thread_snapshot: ThreadSnapshot
) -> None:
    storage = RunStorage(tmp_path / "runs", now=lambda: "2026-01-01T00:00:00+00:00")
    run_id = storage.create_run({"example": True}, run_id="test-run")
    storage.write_thread(run_id, thread_snapshot)
    storage.set_stage(run_id, "ingest", "completed", input_hash="input-hash")

    manifest = storage.read_json(run_id, "manifest.json")
    assert isinstance(manifest, dict)
    assert manifest["config_hash"]
    assert manifest["artifacts"]["thread"]["sha256"]
    assert manifest["stages"]["ingest"]["input_hash"] == "input-hash"
    assert storage.read_thread(run_id) == thread_snapshot
    assert storage.stage_is_current(run_id, "ingest", "input-hash", ("thread",))

    storage.artifact_path(run_id, "thread.json").write_text("corrupt", encoding="utf-8")

    assert not storage.stage_is_current(run_id, "ingest", "input-hash", ("thread",))


@pytest.mark.parametrize("name", ["../secret", "/tmp/secret", "nested/file.json"])
def test_artifact_paths_cannot_escape_run(tmp_path: Path, name: str) -> None:
    storage = RunStorage(tmp_path / "runs")
    run_id = storage.create_run({}, run_id="safe-run")

    with pytest.raises(StorageError):
        storage.artifact_path(run_id, name)


def test_run_id_cannot_escape_storage(tmp_path: Path) -> None:
    storage = RunStorage(tmp_path / "runs")

    with pytest.raises(StorageError):
        storage.run_dir("../outside")
