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

from redditsurfer.domain import (
    JsonValue,
    NarrationScript,
    SelectionResult,
    SpeechArtifact,
    ThreadSnapshot,
)
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

    def read_selection(self, run_id: str) -> SelectionResult:
        try:
            return SelectionResult.from_dict(self.read_json(run_id, "selection.json"))
        except ValueError as exc:
            raise StorageError(f"Selection artifact is invalid for run {run_id}.") from exc

    def write_script(self, run_id: str, script: NarrationScript, report: str) -> None:
        self.write_json(run_id, "script.json", script.to_dict())
        self.write_text(run_id, "script.txt", report)
        self.record_artifact(run_id, "script", "script.json")
        self.record_artifact(run_id, "script_report", "script.txt")

    def read_script(self, run_id: str) -> NarrationScript:
        try:
            return NarrationScript.from_dict(self.read_json(run_id, "script.json"))
        except ValueError as exc:
            raise StorageError(f"Script artifact is invalid for run {run_id}.") from exc

    def write_speech(self, run_id: str, speech: SpeechArtifact) -> None:
        self.write_json(run_id, "speech.json", speech.to_dict())
        self.record_artifact(run_id, "speech", "speech.json")
        self.record_artifact(run_id, "narration_audio", speech.audio_path)

    def read_speech(self, run_id: str) -> SpeechArtifact:
        try:
            return SpeechArtifact.from_dict(self.read_json(run_id, "speech.json"))
        except ValueError as exc:
            raise StorageError(f"Speech artifact is invalid for run {run_id}.") from exc

    def read_json(self, run_id: str, name: str) -> object:
        path = self.artifact_path(run_id, name)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StorageError(f"Artifact does not exist: {name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"Could not read artifact: {name}") from exc

    def write_json(self, run_id: str, name: str, value: object) -> Path:
        self.artifact_path(run_id, name)
        return self.write_json_internal(run_id, name, value)

    def write_json_internal(self, run_id: str, relative_path: str, value: object) -> Path:
        try:
            encoded = (
                json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            ).encode()
        except (TypeError, ValueError) as exc:
            raise StorageError(f"Could not serialize artifact: {relative_path}") from exc
        return self.write_bytes(run_id, relative_path, encoded)

    def read_json_internal(self, run_id: str, relative_path: str) -> object:
        path = self.internal_path(run_id, relative_path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StorageError(f"Artifact does not exist: {relative_path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"Could not read artifact: {relative_path}") from exc

    def write_text(self, run_id: str, relative_path: str, value: str) -> Path:
        return self.write_bytes(run_id, relative_path, value.encode())

    def write_bytes(self, run_id: str, relative_path: str, value: bytes) -> Path:
        path = self.internal_path(run_id, relative_path, create_parent=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except OSError as exc:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
            raise StorageError(f"Could not write artifact: {relative_path}") from exc
        return path

    def artifact_path(self, run_id: str, name: str) -> Path:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise StorageError(f"Invalid artifact name: {name!r}")
        run_dir = self.require_run(run_id)
        candidate = (run_dir / name).resolve()
        if candidate.parent != run_dir:
            raise StorageError("Artifact path escapes the run directory.")
        return candidate

    def internal_path(
        self, run_id: str, relative_path: str, *, create_parent: bool = False
    ) -> Path:
        requested = Path(relative_path)
        if requested.is_absolute() or relative_path in {"", ".", ".."}:
            raise StorageError(f"Invalid internal artifact path: {relative_path!r}")
        run_dir = self.require_run(run_id)
        candidate = (run_dir / requested).resolve()
        if not candidate.is_relative_to(run_dir) or candidate == run_dir:
            raise StorageError("Internal artifact path escapes the run directory.")
        if create_parent:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise StorageError(
                    f"Could not create artifact directory: {candidate.parent}"
                ) from exc
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

    def mark_stages_stale(
        self, run_id: str, stages_to_invalidate: tuple[str, ...], *, reason: str
    ) -> None:
        manifest = self._manifest(run_id)
        stages = manifest.setdefault("stages", {})
        if not isinstance(stages, dict):
            raise StorageError("Run manifest stages are invalid.")
        changed = False
        for stage in stages_to_invalidate:
            if stage not in stages:
                continue
            stages[stage] = {
                "status": "stale",
                "updated_at": self._now(),
                "message": reason,
            }
            changed = True
        if changed:
            manifest["status"] = "stale"
            manifest["updated_at"] = self._now()
            self.write_json(run_id, "manifest.json", manifest)

    def record_artifact(self, run_id: str, key: str, name: str) -> None:
        manifest = self._manifest(run_id)
        artifacts = manifest.setdefault("artifacts", {})
        if not isinstance(artifacts, dict):
            raise StorageError("Run manifest artifacts are invalid.")
        path = self.internal_path(run_id, name)
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
