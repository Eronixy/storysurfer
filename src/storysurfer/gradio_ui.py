"""Minimal Gradio browser workflow over StorySurfer's durable services."""

from __future__ import annotations

import re
import secrets
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import gradio as gr
from gradio.utils import get_upload_folder

from storysurfer.config import AppConfig
from storysurfer.errors import StorySurferError, WebError
from storysurfer.pipeline import (
    Renderer,
    SourceFactory,
    SpeechFactory,
    narration_groups,
    revise_script,
    revise_selection,
    script_run,
)
from storysurfer.reddit.url import parse_reddit_reference
from storysurfer.speech.voices import (
    EdgeVoice,
    fetch_edge_voices,
    language_choices_for_voice,
    locale_for_voice,
    voice_choices,
)
from storysurfer.storage import RunStorage
from storysurfer.web.artifacts import ARTIFACT_ALLOWLIST, write_public_manifest
from storysurfer.web.jobs import JobStore, JobWorker
from storysurfer.web.models import ProjectSettings
from storysurfer.web.service import PipelineJobExecutor, ProjectRepository

CSS = """
html { overflow-y: scroll; scrollbar-gutter: stable; }
.gradio-container {
  box-sizing: border-box !important;
  width: calc(100% - 2rem) !important;
  max-width: 1180px !important;
  min-width: 0 !important;
  margin: 0 auto !important;
}
.ss-tabs, .ss-tab-content {
  box-sizing: border-box !important;
  width: 100% !important;
  max-width: 100% !important;
  min-width: 0 !important;
}
.ss-tab-content > div { width: 100% !important; max-width: 100% !important; min-width: 0; }
.ss-hero h1 { letter-spacing: -.04em; } .ss-hero p { color: #666; }
.ss-video video { max-height: 68vh !important; object-fit: contain !important; }
#delete-project-modal {
  position: fixed !important;
  inset: 0 !important;
  z-index: 10000 !important;
  width: 100% !important;
  max-width: none !important;
  height: 100dvh !important;
  min-height: 0 !important;
  padding: 1rem !important;
  margin: 0 !important;
  border: 0 !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  align-items: center !important;
  justify-content: center !important;
  background: rgba(15, 23, 42, .72) !important;
}
#delete-project-dialog {
  box-sizing: border-box !important;
  width: min(32rem, calc(100vw - 2rem)) !important;
  max-width: 32rem !important;
  height: auto !important;
  min-height: 0 !important;
  max-height: calc(100dvh - 2rem) !important;
  flex: 0 0 auto !important;
  align-self: center !important;
  overflow-y: auto !important;
  gap: 1rem !important;
  padding: 1.5rem !important;
  border: 1px solid #dc2626 !important;
  border-radius: 1rem !important;
  background: var(--block-background-fill) !important;
  box-shadow: 0 24px 64px rgba(0, 0, 0, .35) !important;
}
#delete-project-dialog .ss-delete-copy,
#delete-project-dialog .ss-delete-copy > div,
#delete-project-dialog .ss-delete-copy .prose {
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
  border: 0 !important;
  box-shadow: none !important;
  background: transparent !important;
}
#delete-project-actions {
  gap: .75rem !important;
  background: transparent !important;
}
#delete-project-trigger,
#delete-project-trigger button,
#confirm-delete-project,
#confirm-delete-project button {
  color: #fff !important;
  border-color: #dc2626 !important;
  background: #dc2626 !important;
}
#delete-project-trigger:hover,
#delete-project-trigger button:hover,
#confirm-delete-project:hover,
#confirm-delete-project button:hover {
  border-color: #b91c1c !important;
  background: #b91c1c !important;
}
button:focus-visible, input:focus-visible, textarea:focus-visible {
  outline: 3px solid #2563eb !important; outline-offset: 2px !important;
}
@media (max-width: 640px) {
  .gradio-container { width: calc(100% - 1rem) !important; }
}
"""


@dataclass(slots=True)
class GradioApplication:
    demo: gr.Blocks
    worker: JobWorker
    config: AppConfig
    worker_started: bool = False

    def start(self) -> None:
        if not self.worker_started:
            self.worker.start()
            self.worker_started = True

    def close(self) -> None:
        if self.worker_started:
            self.worker.stop()
            self.worker_started = False

    def launch(self, host: str, port: int, inbrowser: bool) -> None:
        self.start()
        try:
            self.demo.launch(
                server_name=host,
                server_port=port,
                inbrowser=inbrowser,
                share=False,
                strict_cors=True,
                show_error=False,
                enable_monitoring=False,
                footer_links=[],
                max_file_size=f"{self.config.web.upload_max_megabytes}mb",
                blocked_paths=[
                    str(Path(".env").resolve()),
                    str(Path("config.yaml").resolve()),
                    str(self.config.web.database_path.resolve()),
                ],
            )
        finally:
            self.close()


class _SessionTokens:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._lock = threading.Lock()

    def issue(self, session: str | None) -> str:
        if not session:
            raise WebError("Browser session unavailable. Refresh and try again.")
        with self._lock:
            return self._tokens.setdefault(session, secrets.token_urlsafe(32))

    def verify(self, token: str, session: str | None) -> None:
        if not session:
            raise WebError("Browser session unavailable. Refresh and try again.")
        with self._lock:
            expected = self._tokens.get(session)
        if expected is None or not secrets.compare_digest(expected, token):
            raise WebError("This browser action expired or failed CSRF validation.")


def create_gradio_app(
    config: AppConfig,
    *,
    source_factory: SourceFactory | None = None,
    speech_factory: SpeechFactory | None = None,
    renderer: Renderer | None = None,
    start_worker: bool = True,
    upload_root: Path | None = None,
) -> GradioApplication:
    storage = RunStorage(config.storage.runs_dir)
    projects = ProjectRepository(storage)
    jobs = JobStore(config.web.database_path)
    worker = JobWorker(
        jobs,
        PipelineJobExecutor(
            config,
            storage,
            projects,
            source_factory=source_factory,
            speech_factory=speech_factory,
            renderer=renderer,
        ),
        poll_seconds=config.web.refresh_interval_seconds,
    )
    root = (upload_root or Path(get_upload_folder())).resolve()
    demo = _build_ui(config, storage, projects, jobs, worker, root)
    result = GradioApplication(demo, worker, config)
    if start_worker:
        result.start()
    return result


def _build_ui(
    config: AppConfig,
    storage: RunStorage,
    projects: ProjectRepository,
    jobs: JobStore,
    worker: JobWorker,
    upload_root: Path,
) -> gr.Blocks:
    sessions = _SessionTokens()
    configured_locale = locale_for_voice((), config.speech.voice)
    voice_catalog: tuple[EdgeVoice, ...] = (
        EdgeVoice(
            name=config.speech.voice,
            locale=configured_locale,
            language=configured_locale,
            gender="Configured",
        ),
    )

    def run_choices() -> list[str]:
        return list(storage.list_run_ids())

    def dashboard_rows() -> list[list[str | int]]:
        rows: list[list[str | int]] = []
        for run_id in run_choices():
            job = jobs.latest_for_run(run_id)
            try:
                project = projects.read(run_id)
                preset: str = project.preset
                target_seconds = project.target_duration_seconds
            except StorySurferError:
                preset = "CLI run"
                target_seconds = 0
            rows.append(
                [
                    run_id,
                    job.state if job else "created",
                    job.stage if job else "created",
                    job.progress if job else 0,
                    preset,
                    target_seconds,
                ]
            )
        return rows

    def issue(request: gr.Request) -> str:
        return sessions.issue(request.session_hash)

    def verify(token: str, request: gr.Request) -> None:
        sessions.verify(token, request.session_hash)

    def voice_controls(selected: str) -> tuple[Any, Any]:
        language_options, locale = language_choices_for_voice(voice_catalog, selected)
        choices, value = voice_choices(voice_catalog, locale, selected=selected)
        return (
            gr.Dropdown(choices=language_options, value=locale),
            gr.Dropdown(choices=choices, value=value),
        )

    with gr.Blocks(
        title="StorySurfer",
        fill_width=True,
        analytics_enabled=False,
        delete_cache=(3600, 86400),
        theme=gr.themes.Base(primary_hue="blue", neutral_hue="stone"),
        css=CSS,
    ) as demo:
        csrf = gr.State("")
        gr.Markdown(
            "# StorySurfer\nTurn a public Reddit story and licensed gameplay into a "
            "source-linked vertical video.",
            elem_classes="ss-hero",
        )
        with gr.Row():
            run = gr.Dropdown(label="Current project")
            refresh = gr.Button("Refresh")
        with gr.Tabs(elem_classes="ss-tabs"):
            with gr.Tab("Projects", elem_classes="ss-tab-content"):
                dashboard = gr.Dataframe(
                    headers=["Run", "State", "Stage", "Progress %", "Preset", "Target s"],
                    datatype=["str", "str", "str", "number", "str", "number"],
                    type="array",
                    interactive=False,
                    wrap=True,
                )
                url = gr.Textbox(label="Reddit post URL")
                update_urls = gr.Textbox(
                    label="Update post URLs (optional, one per line)",
                    lines=3,
                    info="Each update must be a public Reddit post by the original poster.",
                )
                upload = gr.File(
                    label="Gameplay video",
                    file_types=[".mp4", ".mov", ".mkv", ".webm"],
                    type="filepath",
                )
                with gr.Row():
                    preset = gr.Radio(
                        ["minecraft", "subway"], value="minecraft", label="Crop preset"
                    )
                    duration = gr.Slider(
                        15,
                        3600,
                        config.selection.target_duration_seconds,
                        step=5,
                        label="Target duration (seconds)",
                    )
                with gr.Row():
                    requested_op_exchanges = gr.Slider(
                        0,
                        50,
                        config.selection.requested_op_exchanges,
                        step=1,
                        label="Requested OP exchanges",
                    )
                    requested_comments = gr.Slider(
                        0,
                        50,
                        config.selection.requested_comments,
                        step=1,
                        label="Requested standalone comments",
                    )
                with gr.Row():
                    create_language = gr.Dropdown(
                        choices=[(configured_locale, configured_locale)],
                        value=configured_locale,
                        label="Narration language / locale",
                        info="Show voices specialized for this locale.",
                    )
                    voice = gr.Dropdown(
                        choices=[(config.speech.voice, config.speech.voice)],
                        value=config.speech.voice,
                        label="Edge TTS voice",
                    )
                voice_status = gr.Markdown("Loading the Edge TTS voice catalog…")
                create = gr.Button("Create and prepare", variant="primary")
                delete_project = gr.Button(
                    "Delete selected project",
                    variant="stop",
                    elem_id="delete-project-trigger",
                )
                with (
                    gr.Column(visible=False, elem_id="delete-project-modal") as delete_modal,
                    gr.Column(variant="compact", elem_id="delete-project-dialog"),
                ):
                    gr.Markdown(
                        "## Permanently delete project?", elem_classes="ss-delete-copy"
                    )
                    delete_prompt = gr.Markdown(elem_classes="ss-delete-copy")
                    gr.Markdown(
                        "This removes all generated files and cannot be undone.",
                        elem_classes="ss-delete-copy",
                    )
                    with gr.Row(elem_id="delete-project-actions"):
                        cancel_delete = gr.Button("Cancel", elem_id="cancel-delete-project")
                        confirm_delete = gr.Button(
                            "Permanently delete project",
                            variant="stop",
                            elem_id="confirm-delete-project",
                        )
            with gr.Tab("Review", elem_classes="ss-tab-content"):
                load_review = gr.Button("Load selected project for review")
                source = gr.Textbox(label="Original post", lines=12, interactive=False)
                candidates = gr.Dataframe(
                    headers=["ID", "Included", "Kind", "Score", "Context", "Reasons"],
                    datatype=["str", "bool", "str", "number", "str", "str"],
                    type="array",
                    interactive=True,
                    static_columns=[0, 2, 3, 4, 5],
                    wrap=True,
                    label="Relevant comment candidates — edit Included only",
                )
                gr.Markdown(
                    "Check **Included** to narrate a candidate. A commenter message and "
                    "OP's direct reply remain one atomic exchange."
                )
                save_selection = gr.Button("Save selection and rebuild script")
                script = gr.Dataframe(
                    headers=["Group", "Segment ID", "Speaker", "Spoken text", "Source excerpt"],
                    datatype=["str", "str", "str", "str", "str"],
                    type="array",
                    interactive=True,
                    wrap=True,
                    label="Narration script — edit only Spoken text",
                )
                save_script = gr.Button("Save table script revision")
                centralized_script = gr.Textbox(
                    label="Centralized narration script",
                    lines=20,
                    info=(
                        "Edit narration in one place. Keep each [[segment-id]] marker unchanged."
                    ),
                )
                save_centralized_script = gr.Button("Save centralized script revision")
            with gr.Tab("Style & render", elem_classes="ss-tab-content"):
                with gr.Row():
                    style_language = gr.Dropdown(
                        choices=[(configured_locale, configured_locale)],
                        value=configured_locale,
                        label="Narration language / locale",
                        info="Show voices specialized for this locale.",
                    )
                    style_voice = gr.Dropdown(
                        choices=[(config.speech.voice, config.speech.voice)],
                        value=config.speech.voice,
                        label="Edge TTS voice",
                    )
                with gr.Row():
                    color = gr.ColorPicker(
                        config.captions.highlight_color, label="Spoken-word highlight"
                    )
                    margin = gr.Slider(
                        0,
                        1500,
                        config.captions.margin_bottom,
                        step=10,
                        label="Caption bottom margin",
                    )
                crop = gr.Slider(-1, 1, 0, step=0.05, label="Gameplay crop offset")
                audio = gr.Checkbox(False, label="Keep and duck licensed gameplay audio")
                save_style = gr.Button("Save style")
                with gr.Row():
                    preview = gr.Button("Build preview", variant="primary")
                    rights = gr.Checkbox(
                        False,
                        label="I confirm I have rights to use the source content and assets",
                    )
                    final = gr.Button("Render final")
                    cancel = gr.Button("Cancel active job", variant="stop")
                progress = gr.Slider(0, 100, 0, label="Job progress", interactive=False)
                status = gr.Markdown("Choose or create a project.")
                with gr.Row():
                    preview_video = gr.Video(label="Preview", interactive=False)
                    final_video = gr.Video(label="Final video", interactive=False)
            with gr.Tab("Artifacts", elem_classes="ss-tab-content"):
                load_artifacts = gr.Button("Load public artifacts")
                artifacts = gr.File(label="Downloads", file_count="multiple", interactive=False)
        timer = gr.Timer(config.web.refresh_interval_seconds)

        def initialize(request: gr.Request) -> tuple[str, Any, list[list[str | int]]]:
            choices = run_choices()
            return (
                issue(request),
                gr.Dropdown(choices=choices, value=choices[0] if choices else None),
                dashboard_rows(),
            )

        async def load_voice_catalog() -> tuple[Any, Any, Any, Any, str]:
            nonlocal voice_catalog
            try:
                voice_catalog = await fetch_edge_voices()
                language_control, voice_control = voice_controls(config.speech.voice)
                return (
                    language_control,
                    voice_control,
                    gr.skip(),
                    gr.skip(),
                    _ok(f"Loaded {len(voice_catalog)} Edge TTS voices."),
                )
            except StorySurferError as exc:
                language_control, voice_control = voice_controls(config.speech.voice)
                return (
                    language_control,
                    voice_control,
                    gr.skip(),
                    gr.skip(),
                    _error(exc),
                )

        def filter_voices(locale: str, selected: str | None) -> Any:
            choices, value = voice_choices(voice_catalog, locale, selected=selected)
            return gr.Dropdown(choices=choices, value=value)

        def refresh_runs() -> tuple[Any, list[list[str | int]]]:
            return gr.Dropdown(choices=run_choices()), dashboard_rows()

        def open_delete_modal(run_id: str | None) -> tuple[Any, str, str]:
            try:
                chosen = _run_id(run_id)
                storage.require_run(chosen)
                return (
                    gr.Column(visible=True),
                    f"Selected project: `{chosen}`",
                    _ok("Confirm deletion in the dialog."),
                )
            except (StorySurferError, ValueError) as exc:
                return gr.Column(visible=False), "", _error(exc)

        def close_delete_modal() -> Any:
            return gr.Column(visible=False)

        def delete_selected_project(
            token: str,
            run_id: str | None,
            request: gr.Request,
        ) -> tuple[str, Any, Any, Any]:
            try:
                verify(token, request)
                chosen = _run_id(run_id)
                storage.require_run(chosen)
                jobs.request_cancel(chosen)
                jobs.delete_for_run(chosen)
                storage.delete_run(chosen)
                return (
                    _ok(f"Permanently deleted project {chosen}."),
                    gr.Dropdown(choices=run_choices(), value=None),
                    dashboard_rows(),
                    gr.Column(visible=False),
                )
            except (StorySurferError, ValueError) as exc:
                return _error(exc), gr.skip(), gr.skip(), gr.Column(visible=False)

        def create_project(
            token: str,
            source_url: str,
            update_post_urls: str,
            uploaded: str | None,
            selected_preset: str,
            seconds: float,
            selected_voice: str,
            op_exchange_count: float,
            comment_count: float,
            request: gr.Request,
        ) -> tuple[str, Any, list[list[str | int]]]:
            try:
                verify(token, request)
                if uploaded is None:
                    raise WebError("Choose a gameplay video.")
                main_reference = parse_reddit_reference(source_url)
                update_references = tuple(
                    parse_reddit_reference(line.strip())
                    for line in update_post_urls.splitlines()
                    if line.strip()
                )
                update_ids = [item.submission_id for item in update_references]
                if main_reference.submission_id in update_ids or len(update_ids) != len(
                    set(update_ids)
                ):
                    raise WebError(
                        "Update post URLs must be unique and different from the main post."
                    )
                source_path = _validate_upload(
                    Path(uploaded), upload_root, config.web.upload_limit_bytes
                )
                suffix = source_path.suffix.lower()
                if suffix not in {".mp4", ".mov", ".mkv", ".webm"}:
                    raise WebError("Gameplay must be MP4, MOV, MKV, or WebM.")
                project = ProjectSettings.from_dict(
                    {
                        "schema_version": 1,
                        "source_url": main_reference.canonical_url,
                        "update_urls": [item.canonical_url for item in update_references],
                        "staging_path": f"staging/gameplay{suffix}",
                        "background_path": f"assets/gameplay{suffix}",
                        "preset": selected_preset,
                        "target_duration_seconds": int(seconds),
                        "voice": selected_voice,
                        "requested_op_exchanges": int(op_exchange_count),
                        "requested_comments": int(comment_count),
                    }
                )
                run_id = storage.create_run(project.apply(config).public_dict())
                storage.set_stage(run_id, "upload", "running")
                try:
                    target = storage.internal_path(run_id, project.staging_path, create_parent=True)
                    _copy_upload(source_path, target, config.web.upload_limit_bytes)
                    storage.record_artifact(run_id, "background_staging", project.staging_path)
                    projects.write(run_id, project)
                    storage.set_stage(
                        run_id,
                        "upload",
                        "queued",
                        message="Upload staged for worker-side media validation.",
                    )
                    job = jobs.enqueue(run_id, "prepare", projects.job_key(run_id, "prepare"))
                except Exception:
                    storage.set_stage(
                        run_id,
                        "upload",
                        "failed",
                        message="Gameplay upload could not be safely staged.",
                    )
                    raise
                worker.wake()
                return (
                    _ok(f"Created {run_id}; {job.message}"),
                    gr.Dropdown(choices=run_choices(), value=run_id),
                    dashboard_rows(),
                )
            except (StorySurferError, OSError, ValueError) as exc:
                return _error(exc), gr.Dropdown(), dashboard_rows()

        def review(
            run_id: str | None,
        ) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, str]:
            try:
                chosen = _run_id(run_id)
                thread, selection = storage.read_thread(chosen), storage.read_selection(chosen)
                comments = {item.id: item for item in thread.comments}
                rows = []
                for item in selection.candidates:
                    context = "\n".join(
                        f"{'OP' if comments[key].is_op else 'Commenter'}: {comments[key].body}"
                        for key in item.source_ids
                        if key in comments
                    )
                    rows.append(
                        [
                            item.id,
                            item.selected,
                            item.kind,
                            item.score,
                            context,
                            ", ".join(item.reason_codes),
                        ]
                    )
                project = projects.read(chosen)
                review_script = storage.read_script(chosen)
                language_control, voice_control = voice_controls(project.voice)
                return (
                    f"TITLE\n{thread.submission.title}\n\nPOST\n{thread.submission.body}",
                    rows,
                    _script_rows(review_script),
                    _centralized_script_text(review_script),
                    language_control,
                    voice_control,
                    project.highlight_color,
                    project.margin_bottom,
                    _ok("Review loaded."),
                )
            except (StorySurferError, ValueError) as exc:
                language_control, voice_control = voice_controls(config.speech.voice)
                return (
                    "",
                    [],
                    [],
                    "",
                    language_control,
                    voice_control,
                    "#FFD54F",
                    430,
                    _error(exc),
                )

        def select_sources(
            token: str, run_id: str | None, rows: list[list[Any]], request: gr.Request
        ) -> tuple[list[list[str]], str, str]:
            try:
                verify(token, request)
                chosen = _run_id(run_id)
                revise_selection(chosen, _selected_candidate_ids(rows), storage)
                result = script_run(chosen, projects.read(chosen).apply(config), storage)
                return (
                    _script_rows(result),
                    _centralized_script_text(result),
                    _ok("Selection and script saved."),
                )
            except (StorySurferError, ValueError) as exc:
                return [], "", _error(exc)

        def edit_script(
            token: str,
            run_id: str | None,
            rows: list[list[Any]],
            centralized_value: str,
            request: gr.Request,
        ) -> tuple[list[list[str]], str, str]:
            try:
                verify(token, request)
                chosen = _run_id(run_id)
                current = storage.read_script(chosen)
                texts = {str(row[1]): str(row[3]) for row in rows if len(row) >= 4}
                groups = tuple(group[0].id for group in narration_groups(current))
                result = revise_script(
                    chosen, texts, groups, projects.read(chosen).apply(config), storage
                )
                return (
                    _script_rows(result),
                    _centralized_script_text(result),
                    _ok("Table script revision saved."),
                )
            except (StorySurferError, ValueError) as exc:
                return rows or [], centralized_value, _error(exc)

        def edit_centralized_script(
            token: str,
            run_id: str | None,
            value: str,
            request: gr.Request,
        ) -> tuple[list[list[str]], str, str]:
            current_rows: list[list[str]] = []
            try:
                verify(token, request)
                chosen = _run_id(run_id)
                current = storage.read_script(chosen)
                current_rows = _script_rows(current)
                texts = _parse_centralized_script(value, current)
                groups = tuple(group[0].id for group in narration_groups(current))
                result = revise_script(
                    chosen, texts, groups, projects.read(chosen).apply(config), storage
                )
                return (
                    _script_rows(result),
                    _centralized_script_text(result),
                    _ok("Centralized script revision saved."),
                )
            except (StorySurferError, ValueError) as exc:
                return current_rows, value, _error(exc)

        def style(
            token: str,
            run_id: str | None,
            selected_voice: str,
            offset: float,
            retain_audio: bool,
            highlight: str,
            bottom: float,
            request: gr.Request,
        ) -> str:
            try:
                verify(token, request)
                chosen, current = _run_id(run_id), projects.read(_run_id(run_id))
                project = ProjectSettings.from_dict(
                    {
                        **current.to_dict(),
                        "voice": selected_voice,
                        "crop_offset": offset,
                        "retain_background_audio": retain_audio,
                        "highlight_color": highlight,
                        "margin_bottom": int(bottom),
                    }
                )
                projects.write(chosen, project)
                stale: tuple[str, ...]
                if project.voice != current.voice:
                    stale = (
                        "synthesize",
                        "caption",
                        "render_preview",
                        "render_final",
                        "verify_preview",
                        "verify_final",
                    )
                elif (
                    project.highlight_color != current.highlight_color
                    or project.margin_bottom != current.margin_bottom
                ):
                    stale = (
                        "caption",
                        "render_preview",
                        "render_final",
                        "verify_preview",
                        "verify_final",
                    )
                else:
                    stale = (
                        "render_preview",
                        "render_final",
                        "verify_preview",
                        "verify_final",
                    )
                storage.mark_stages_stale(
                    chosen,
                    stale,
                    reason="Browser settings changed.",
                )
                return _ok("Style saved; affected stages are stale.")
            except (StorySurferError, ValueError) as exc:
                return _error(exc)

        def enqueue(
            token: str,
            run_id: str | None,
            kind: str,
            acknowledged: bool,
            request: gr.Request,
        ) -> str:
            try:
                verify(token, request)
                chosen = _run_id(run_id)
                if kind == "final" and not acknowledged:
                    raise WebError("Acknowledge content and asset rights before final rendering.")
                storage.read_script(chosen)
                job = jobs.enqueue(chosen, kind, projects.job_key(chosen, kind))
                worker.wake()
                return _ok(f"{kind.title()} job {job.state}: {job.message}")
            except (StorySurferError, ValueError) as exc:
                return _error(exc)

        def preview_job(token: str, run_id: str | None, request: gr.Request) -> str:
            return enqueue(token, run_id, "preview", False, request)

        def final_job(
            token: str, run_id: str | None, acknowledged: bool, request: gr.Request
        ) -> str:
            return enqueue(token, run_id, "final", acknowledged, request)

        def cancel_job(token: str, run_id: str | None, request: gr.Request) -> str:
            try:
                verify(token, request)
                count = jobs.request_cancel(_run_id(run_id))
                return _ok(f"Cancellation requested for {count} job(s).")
            except (StorySurferError, ValueError) as exc:
                return _error(exc)

        def poll(run_id: str | None) -> tuple[str, int, str | None, str | None]:
            if not run_id:
                return "Choose or create a project.", 0, None, None
            job = jobs.latest_for_run(run_id)
            if job is None:
                return "**State:** created", 0, None, None
            preview_path, final_path = (
                storage.artifact_path(run_id, "preview.mp4"),
                storage.artifact_path(run_id, "final.mp4"),
            )
            return (
                f"**State:** {job.state}  \n**Stage:** {job.stage}  \n{job.message}",
                job.progress,
                str(preview_path) if preview_path.is_file() else None,
                str(final_path) if final_path.is_file() else None,
            )

        def downloads(run_id: str | None) -> tuple[list[str], str]:
            chosen = _run_id(run_id)
            paths = [str(write_public_manifest(chosen, storage))]
            paths += [
                str(path)
                for name in sorted(ARTIFACT_ALLOWLIST)
                if (path := storage.artifact_path(chosen, name)).is_file()
            ]
            return paths, _ok(f"Loaded {len(paths)} public artifact(s).")

        demo.load(
            initialize,
            outputs=[csrf, run, dashboard],
            queue=False,
            api_visibility="private",
        )
        demo.load(
            load_voice_catalog,
            outputs=[create_language, voice, style_language, style_voice, voice_status],
            queue=False,
            api_visibility="private",
        )
        create_language.input(
            filter_voices,
            [create_language, voice],
            voice,
            queue=False,
            api_visibility="private",
        )
        style_language.input(
            filter_voices,
            [style_language, style_voice],
            style_voice,
            queue=False,
            api_visibility="private",
        )
        refresh.click(
            refresh_runs,
            outputs=[run, dashboard],
            queue=False,
            api_visibility="private",
        )
        create.click(
            create_project,
            [
                csrf,
                url,
                update_urls,
                upload,
                preset,
                duration,
                voice,
                requested_op_exchanges,
                requested_comments,
            ],
            [status, run, dashboard],
            api_visibility="private",
        )
        delete_project.click(
            open_delete_modal,
            run,
            [delete_modal, delete_prompt, status],
            queue=False,
            api_visibility="private",
        )
        cancel_delete.click(
            close_delete_modal,
            outputs=delete_modal,
            queue=False,
            api_visibility="private",
        )
        confirm_delete.click(
            delete_selected_project,
            [csrf, run],
            [status, run, dashboard, delete_modal],
            api_visibility="private",
        )
        load_review.click(
            review,
            run,
            [
                source,
                candidates,
                script,
                centralized_script,
                style_language,
                style_voice,
                color,
                margin,
                status,
            ],
            api_visibility="private",
        )
        save_selection.click(
            select_sources,
            [csrf, run, candidates],
            [script, centralized_script, status],
            api_visibility="private",
        )
        candidates.input(
            select_sources,
            [csrf, run, candidates],
            [script, centralized_script, status],
            api_visibility="private",
        )
        save_script.click(
            edit_script,
            [csrf, run, script, centralized_script],
            [script, centralized_script, status],
            api_visibility="private",
        )
        save_centralized_script.click(
            edit_centralized_script,
            [csrf, run, centralized_script],
            [script, centralized_script, status],
            api_visibility="private",
        )
        save_style.click(
            style,
            [csrf, run, style_voice, crop, audio, color, margin],
            status,
            api_visibility="private",
        )
        preview.click(preview_job, [csrf, run], status, api_visibility="private")
        final.click(final_job, [csrf, run, rights], status, api_visibility="private")
        cancel.click(cancel_job, [csrf, run], status, api_visibility="private")
        timer.tick(
            poll,
            run,
            [status, progress, preview_video, final_video],
            queue=False,
            trigger_mode="always_last",
            api_visibility="private",
        )
        load_artifacts.click(downloads, run, [artifacts, status], api_visibility="private")
    return cast(gr.Blocks, demo)


def _run_id(value: str | None) -> str:
    if not value:
        raise WebError("Choose a project first.")
    return value


def _validate_upload(source: Path, upload_root: Path, maximum: int) -> Path:
    resolved = source.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(upload_root.resolve()):
        raise WebError("Upload is outside Gradio's protected directory.")
    if resolved.stat().st_size > maximum:
        raise WebError("Gameplay upload exceeds the configured size limit.")
    return resolved


def _copy_upload(source: Path, target: Path, maximum: int) -> None:
    copied = 0
    temporary: Path | None = None
    try:
        with (
            source.open("rb") as input_file,
            tempfile.NamedTemporaryFile(
                "wb", dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as output_file,
        ):
            temporary = Path(output_file.name)
            while chunk := input_file.read(1024 * 1024):
                copied += len(chunk)
                if copied > maximum:
                    raise WebError("Gameplay upload exceeds the configured size limit.")
                output_file.write(chunk)
        if copied == 0:
            raise WebError("Gameplay upload is empty.")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _script_rows(script: Any) -> list[list[str]]:
    groups = {segment.id: group[0].id for group in narration_groups(script) for segment in group}
    return [
        [groups[item.id], item.id, item.speaker_label, item.spoken_text, item.original_excerpt]
        for item in script.segments
    ]


def _selected_candidate_ids(rows: list[list[Any]] | None) -> set[str]:
    return {
        str(row[0])
        for row in rows or []
        if len(row) >= 2 and isinstance(row[0], str) and row[1] is True
    }


def _centralized_script_text(script: Any) -> str:
    return "\n\n".join(f"[[{segment.id}]]\n{segment.spoken_text}" for segment in script.segments)


def _parse_centralized_script(value: str, script: Any) -> dict[str, str]:
    marker = re.compile(r"(?m)^\[\[([^\]\r\n]+)\]\]\s*$")
    matches = list(marker.finditer(value))
    expected = [segment.id for segment in script.segments]
    found = [match.group(1) for match in matches]
    if found != expected:
        raise WebError(
            "Centralized script markers are missing, reordered, or changed. "
            "Reload the project and keep every [[segment-id]] marker unchanged."
        )
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        text = value[start:end].strip()
        if not text:
            raise WebError(f"Centralized script segment is empty: {match.group(1)}")
        result[match.group(1)] = text
    return result


def _ok(message: str) -> str:
    return f"**Status:** {message}"


def _error(exc: Exception) -> str:
    return (
        f"**Could not complete the action:** {exc.display()}"
        if isinstance(exc, StorySurferError)
        else "**Could not complete the action:** Invalid input."
    )
