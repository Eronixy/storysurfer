"""Safe, atomic storage for resumable build runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from redditsurfer.domain import JsonValue, SelectionResult, ThreadSnapshot
from redditsurfer.errors import StorageError

RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunStorage:
    """Owns run IDs, manifests, and containment-checked artifact paths."""

    def __init__(self, root: Path, *, now: Callable[[], str] = utc_now) -> None:
        self.root = root
        self._now = now

    def create_run(
        self, public_config: dict[str, JsonValue], *, run_id: str | None = None
    ) -> str:
        generated_id = run_id or self._new_run_id()
        run_dir = self.run_dir(generated_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            (run_dir / "logs").mkdir()
        except FileExistsError as exc:
            raise StorageError(f"Run already exists: {generated_id}") from exc
        except OSError as exc:
            raise StorageError(f"Could not create run directory: {run_dir}") from exc

        manifest: dict[str, JsonValue] = {
            "schema_version": 1,
            "run_id": generated_id,
            "created_at": self._now(),
            "updated_at": self._now(),
            "status": "created",
            "config": public_config,
            "config_hash": json_hash(public_config),
            "stages": {},
            "artifacts": {},
        }
        self.write_json(generated_id, "manifest.json", manifest)
        return generated_id

    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise StorageError(f"Invalid run ID: {run_id!r}")
        candidate = (self.root / run_id).resolve()
        root = self.root.resolve()
        if candidate.parent != root:
            raise StorageError("Run path escapes the configured storage directory.")
        return candidate

    def require_run(self, run_id: str) -> Path:
        path = self.run_dir(run_id)
        if not path.is_dir():
            raise StorageError(f"Run does not exist: {run_id}")
        return path

    def write_thread(self, run_id: str, snapshot: ThreadSnapshot) -> Path:
        path = self.write_json(run_id, "thread.json", snapshot.to_dict())
        self.record_artifact(run_id, "thread", "thread.json")
        return path

    def read_thread(self, run_id: str) -> ThreadSnapshot:
        try:
            return ThreadSnapshot.from_dict(self.read_json(run_id, "thread.json"))
        except ValueError as exc:
            raise StorageError(f"Thread artifact is invalid for run {run_id}.") from exc

    def write_selection(self, run_id: str, selection: SelectionResult) -> Path:
        path = self.write_json(run_id, "selection.json", selection.to_dict())
        self.record_artifact(run_id, "selection", "selection.json")
        return path

    def read_json(self, run_id: str, name: str) -> object:
        path = self.artifact_path(run_id, name)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StorageError(f"Artifact does not exist: {name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"Could not read artifact: {name}") from exc

    def write_json(self, run_id: str, name: str, value: object) -> Path:
        run_dir = self.require_run(run_id)
        path = self.artifact_path(run_id, name)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=run_dir,
                prefix=f".{name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except (OSError, TypeError, ValueError) as exc:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
            raise StorageError(f"Could not write artifact: {name}") from exc
        return path

    def artifact_path(self, run_id: str, name: str) -> Path:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise StorageError(f"Invalid artifact name: {name!r}")
        run_dir = self.require_run(run_id)
        candidate = (run_dir / name).resolve()
        if candidate.parent != run_dir:
            raise StorageError("Artifact path escapes the run directory.")
        return candidate

    def set_stage(
        self,
        run_id: str,
        stage: str,
        status: str,
        *,
        message: str | None = None,
        input_hash: str | None = None,
    ) -> None:
        manifest = self._manifest(run_id)
        stages = manifest.setdefault("stages", {})
        if not isinstance(stages, dict):
            raise StorageError("Run manifest stages are invalid.")
        record: dict[str, JsonValue] = {"status": status, "updated_at": self._now()}
        if message:
            record["message"] = message
        if input_hash:
            record["input_hash"] = input_hash
        stages[stage] = record
        manifest["status"] = "failed" if status == "failed" else status
        manifest["updated_at"] = self._now()
        self.write_json(run_id, "manifest.json", manifest)

    def record_artifact(self, run_id: str, key: str, name: str) -> None:
        manifest = self._manifest(run_id)
        artifacts = manifest.setdefault("artifacts", {})
        if not isinstance(artifacts, dict):
            raise StorageError("Run manifest artifacts are invalid.")
        path = self.artifact_path(run_id, name)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise StorageError(f"Could not hash artifact: {name}") from exc
        artifacts[key] = {"path": name, "sha256": digest}
        manifest["updated_at"] = self._now()
        self.write_json(run_id, "manifest.json", manifest)

    def _manifest(self, run_id: str) -> dict[str, JsonValue]:
        value = self.read_json(run_id, "manifest.json")
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise StorageError("Run manifest is invalid.")
        return cast(dict[str, JsonValue], value)

    @staticmethod
    def _new_run_id() -> str:
        prefix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{prefix}-{uuid.uuid4().hex[:8]}"


def json_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
