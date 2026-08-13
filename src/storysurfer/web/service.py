"""Web-facing application services and durable job execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from storysurfer.config import AppConfig, MediaConfig
from storysurfer.domain import JsonValue, Timeline
from storysurfer.errors import StorageError, WebError
from storysurfer.media import probe_media
from storysurfer.media.render import render_video
from storysurfer.pipeline import (
    Renderer,
    SourceFactory,
    SpeechFactory,
    caption_run,
    ingest_into_run,
    narrate,
    preview,
    render_final,
    script_run,
    select,
    verify,
)
from storysurfer.storage import RunStorage, json_hash
from storysurfer.web.models import ProjectSettings

Progress = Callable[[str, int, str], None]
CancelCheck = Callable[[], None]


class ProjectRepository:
    def __init__(self, storage: RunStorage) -> None:
        self.storage = storage

    def write(self, run_id: str, project: ProjectSettings) -> None:
        self.storage.write_json_internal(run_id, "project.json", project.to_dict())
        self.storage.record_artifact(run_id, "project", "project.json")

    def read(self, run_id: str) -> ProjectSettings:
        return ProjectSettings.from_dict(
            self.storage.read_json_internal(run_id, "project.json")
        )

    def background(self, run_id: str, project: ProjectSettings) -> Path:
        return self.storage.internal_path(run_id, project.background_path)

    def job_key(self, run_id: str, kind: str) -> str:
        project = self.read(run_id)
        manifest = self.storage.read_manifest(run_id)
        artifacts = manifest.get("artifacts")
        artifact_hashes: dict[str, JsonValue] = {}
        relevant = {
            "prepare": ("background_staging", "project"),
            "preview": ("script", "project", "background"),
            "final": ("script", "project", "background"),
        }.get(kind)
        if relevant is None:
            raise WebError(f"Unsupported job kind: {kind}")
        if isinstance(artifacts, dict):
            for key in relevant:
                record = artifacts.get(key)
                if isinstance(record, dict) and isinstance(record.get("sha256"), str):
                    artifact_hashes[key] = record["sha256"]
        return json_hash(
            {"kind": kind, "project": project.to_dict(), "artifacts": artifact_hashes}
        )


class PipelineJobExecutor:
    def __init__(
        self,
        base_config: AppConfig,
        storage: RunStorage,
        projects: ProjectRepository,
        *,
        source_factory: SourceFactory | None = None,
        speech_factory: SpeechFactory | None = None,
        renderer: Renderer | None = None,
    ) -> None:
        self.base_config = base_config
        self.storage = storage
        self.projects = projects
        self.source_factory = source_factory
        self.speech_factory = speech_factory
        self.renderer = renderer

    def __call__(
        self,
        kind: str,
        run_id: str,
        progress: Progress,
        check_cancelled: CancelCheck,
    ) -> None:
        project = self.projects.read(run_id)
        config = project.apply(self.base_config)
        background = self.projects.background(run_id, project)
        if kind == "prepare":
            if not background.is_file():
                progress("upload", 5, "Validating the staged gameplay video.")
                check_cancelled()
                staged = self.storage.internal_path(run_id, project.staging_path)
                info = probe_media(staged, config.media.ffprobe_path)
                if info.duration_ms > config.web.upload_duration_limit_seconds * 1_000:
                    raise WebError(
                        "Gameplay duration exceeds the configured local upload limit."
                    )
                try:
                    background.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(background)
                except OSError as exc:
                    raise StorageError(
                        "Could not promote the validated gameplay upload."
                    ) from exc
                self.storage.record_artifact(
                    run_id, "background", project.background_path
                )
                self.storage.set_stage(
                    run_id,
                    "upload",
                    "completed",
                    message="Gameplay media validated and promoted.",
                )
            progress("ingest", 10, "Fetching the Reddit thread.")
            check_cancelled()
            ingest_into_run(
                run_id,
                project.source_url,
                config,
                self.storage,
                project.update_urls,
                source_factory=self.source_factory,
            )
            progress("select", 55, "Ranking complete comment exchanges.")
            check_cancelled()
            select(run_id, config, self.storage)
            progress("script", 80, "Creating the source-linked review script.")
            check_cancelled()
            script_run(run_id, config, self.storage)
            progress("review", 95, "Sources and script are ready for review.")
            return
        if kind not in {"preview", "final"}:
            raise WebError(f"Unsupported job kind: {kind}")

        progress("synthesize", 10, "Synthesizing narration with word timing.")
        narrate(
            run_id,
            config,
            self.storage,
            speech_factory=self.speech_factory,
            cancel_check=check_cancelled,
        )
        progress("caption", 45, "Generating animated and accessible captions.")
        check_cancelled()
        caption_run(run_id, config, self.storage)
        progress(f"render_{kind}", 60, f"Rendering the {kind} video.")
        check_cancelled()
        selected_renderer = self.renderer or _cancellable_renderer(check_cancelled)
        if kind == "preview":
            preview(
                run_id,
                background,
                project.preset,
                config,
                self.storage,
                crop_offset=project.crop_offset,
                renderer=selected_renderer,
            )
            progress("verify_preview", 92, "Checking preview media and artifacts.")
            verify(run_id, "preview", config, self.storage)
        else:
            render_final(
                run_id,
                background,
                project.preset,
                config,
                self.storage,
                acknowledge_rights=True,
                crop_offset=project.crop_offset,
                renderer=selected_renderer,
            )
            progress("verify_final", 92, "Checking final media and artifacts.")
            verify(run_id, "final", config, self.storage)


def _cancellable_renderer(check_cancelled: CancelCheck) -> Renderer:
    def renderer(
        run_dir: Path,
        timeline: Timeline,
        media: MediaConfig,
        output_name: str,
    ) -> Path:
        return render_video(
            run_dir,
            timeline,
            media,
            output_name=output_name,
            cancel_check=check_cancelled,
        )

    return renderer
