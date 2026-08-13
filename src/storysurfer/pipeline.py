"""Application services shared by current and future interfaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from storysurfer.captions import build_caption_artifact, render_ass, render_srt
from storysurfer.config import AppConfig, MediaConfig
from storysurfer.domain import (
    CaptionArtifact,
    JsonValue,
    NarrationScript,
    NarrationSegment,
    SelectionResult,
    SpeechArtifact,
    ThreadSnapshot,
    Timeline,
    VerificationReport,
)
from storysurfer.editorial import select_thread
from storysurfer.editorial.clean import SPEECH_CLEANER_VERSION, clean_for_speech
from storysurfer.editorial.script import build_narration_script, render_script_report
from storysurfer.errors import (
    ConfigurationError,
    MediaError,
    RightsError,
    ScriptError,
    SelectionError,
    StorageError,
    StorySurferError,
    VerificationError,
)
from storysurfer.media import build_timeline, media_for_profile, probe_media
from storysurfer.media.quality import verify_rendered_media
from storysurfer.media.render import render_video
from storysurfer.reddit import PrawRedditClient, RedditThreadSource
from storysurfer.reddit.normalize import normalize_thread
from storysurfer.reddit.url import parse_reddit_reference
from storysurfer.speech import SpeechProvider, synthesize_script
from storysurfer.speech.edge import EdgeTTSSpeechProvider
from storysurfer.storage import RunStorage, file_hash, json_hash

SourceFactory = Callable[[AppConfig], RedditThreadSource]
SpeechFactory = Callable[[AppConfig], SpeechProvider]
RenderProfile = Literal["preview", "final"]
Renderer = Callable[[Path, Timeline, MediaConfig, str], Path]

RENDER_STAGES = ("render_preview", "render_final", "verify_preview", "verify_final")


@dataclass(frozen=True, slots=True)
class BuildResult:
    run_id: str
    profile: RenderProfile
    verification: VerificationReport


def ingest(
    source_value: str,
    config: AppConfig,
    storage: RunStorage,
    *,
    source_factory: SourceFactory | None = None,
    run_id: str | None = None,
) -> tuple[str, ThreadSnapshot]:
    """Create a run, fetch one thread, and atomically persist its normalized snapshot."""
    reference = parse_reddit_reference(source_value)
    created_run_id = storage.create_run(config.public_dict(), run_id=run_id)
    input_hash = json_hash(
        {
            "source_url": reference.canonical_url,
            "comment_sort": config.reddit.comment_sort,
            "max_comments": config.reddit.max_comments,
            "replace_more_limit": config.reddit.replace_more_limit,
        }
    )
    storage.set_stage(created_run_id, "ingest", "running", input_hash=input_hash)
    try:
        factory = source_factory or _praw_source
        raw = factory(config).fetch(reference)
        snapshot = normalize_thread(
            raw,
            author_hash_salt=config.reddit.author_hash_salt,
        )
        storage.write_thread(created_run_id, snapshot)
        storage.set_stage(created_run_id, "ingest", "completed", input_hash=input_hash)
    except StorySurferError as exc:
        if exc.hint is None:
            exc.hint = f"Run {created_run_id} was preserved for inspection."
        storage.set_stage(
            created_run_id,
            "ingest",
            "failed",
            message=exc.message,
            input_hash=input_hash,
        )
        raise
    except Exception:
        storage.set_stage(
            created_run_id,
            "ingest",
            "failed",
            message="Unexpected ingestion failure.",
            input_hash=input_hash,
        )
        raise
    return created_run_id, snapshot


def ingest_snapshot(
    snapshot: ThreadSnapshot,
    config: AppConfig,
    storage: RunStorage,
    *,
    run_id: str | None = None,
) -> tuple[str, ThreadSnapshot]:
    """Create a run from an explicit normalized snapshot without a Reddit request."""
    created_run_id = storage.create_run(config.public_dict(), run_id=run_id)
    input_hash = json_hash({"cached_thread": snapshot.to_dict()})
    storage.set_stage(created_run_id, "ingest", "running", input_hash=input_hash)
    try:
        storage.write_thread(created_run_id, snapshot)
        storage.set_stage(created_run_id, "ingest", "completed", input_hash=input_hash)
    except StorySurferError as exc:
        storage.set_stage(
            created_run_id,
            "ingest",
            "failed",
            message=exc.message,
            input_hash=input_hash,
        )
        raise
    return created_run_id, snapshot


def ingest_existing(
    run_id: str,
    source_value: str,
    config: AppConfig,
    storage: RunStorage,
    *,
    source_factory: SourceFactory | None = None,
) -> ThreadSnapshot:
    """Fetch and persist a Reddit snapshot into a server-created empty web run."""
    storage.require_run(run_id)
    reference = parse_reddit_reference(source_value)
    input_hash = json_hash(
        {
            "source_url": reference.canonical_url,
            "comment_sort": config.reddit.comment_sort,
            "max_comments": config.reddit.max_comments,
            "replace_more_limit": config.reddit.replace_more_limit,
        }
    )
    if storage.stage_is_current(run_id, "ingest", input_hash, ("thread",)):
        return storage.read_thread(run_id)
    statuses = storage.stage_statuses(run_id)
    if statuses.get("ingest") == "completed":
        raise StorageError("A completed web run cannot be repointed to a different Reddit source.")
    storage.set_stage(run_id, "ingest", "running", input_hash=input_hash)
    try:
        factory = source_factory or _praw_source
        raw = factory(config).fetch(reference)
        snapshot = normalize_thread(
            raw,
            author_hash_salt=config.reddit.author_hash_salt,
        )
        storage.write_thread(run_id, snapshot)
        storage.set_stage(run_id, "ingest", "completed", input_hash=input_hash)
        return snapshot
    except StorySurferError as exc:
        storage.set_stage(
            run_id,
            "ingest",
            "failed",
            message=exc.message,
            input_hash=input_hash,
        )
        raise


ingest_into_run = ingest_existing


def revise_selection(
    run_id: str,
    selected_candidate_ids: set[str],
    storage: RunStorage,
) -> SelectionResult:
    """Persist a human selection while keeping each candidate exchange atomic."""
    current = storage.read_selection(run_id)
    known_ids = {candidate.id for candidate in current.candidates}
    unknown = selected_candidate_ids - known_ids
    if unknown:
        raise SelectionError(f"Selection contains unknown candidates: {', '.join(sorted(unknown))}")
    candidates = tuple(
        replace(candidate, selected=candidate.id in selected_candidate_ids)
        for candidate in current.candidates
    )
    revised = replace(
        current,
        candidates=candidates,
        selected_comment_words=sum(
            candidate.word_count for candidate in candidates if candidate.selected
        ),
        warnings=tuple(dict.fromkeys((*current.warnings, "Selection reviewed in the browser."))),
        manually_edited=True,
    )
    storage.write_selection(run_id, revised)
    storage.set_stage(
        run_id,
        "select",
        "completed",
        input_hash=json_hash({"human_selection": revised.to_dict()}),
    )
    storage.mark_stages_stale(
        run_id,
        ("script", "synthesize", "caption", *RENDER_STAGES),
        reason="Human selection changed; regenerate downstream artifacts.",
    )
    return revised


def narration_groups(
    script: NarrationScript,
) -> tuple[tuple[NarrationSegment, ...], ...]:
    """Return reorderable groups without separating commenter/OP exchanges."""
    groups: list[tuple[NarrationSegment, ...]] = []
    index = 0
    while index < len(script.segments):
        segment = script.segments[index]
        if (
            segment.kind == "comment"
            and index + 1 < len(script.segments)
            and script.segments[index + 1].kind == "op_reply"
        ):
            groups.append((segment, script.segments[index + 1]))
            index += 2
        else:
            groups.append((segment,))
            index += 1
    return tuple(groups)


def revise_script(
    run_id: str,
    spoken_text_by_segment: dict[str, str],
    ordered_group_ids: tuple[str, ...],
    config: AppConfig,
    storage: RunStorage,
) -> NarrationScript:
    """Save a source-preserving script revision and reorder only whole groups."""
    current = storage.read_script(run_id)
    current_ids = {segment.id for segment in current.segments}
    if set(spoken_text_by_segment) != current_ids:
        raise ScriptError("Script edit does not contain every current narration segment.")
    groups = narration_groups(current)
    by_group_id = {group[0].id: group for group in groups}
    if set(ordered_group_ids) != set(by_group_id) or len(ordered_group_ids) != len(by_group_id):
        raise ScriptError("Script group order is missing or contains unknown groups.")
    if ordered_group_ids[:2] != tuple(group[0].id for group in groups[:2]):
        raise ScriptError("The title and post must remain at the start of the narration.")
    ordered_segments_list: list[NarrationSegment] = []
    for group_id in ordered_group_ids:
        for segment in by_group_id[group_id]:
            reviewed = _reviewed_text(spoken_text_by_segment[segment.id])
            cleaned = clean_for_speech(reviewed, pronunciations=config.speech.pronunciations)
            if not cleaned.text:
                raise ScriptError("Narration segments cannot be empty after redaction.")
            ordered_segments_list.append(
                replace(
                    segment,
                    spoken_text=cleaned.text,
                    redactions=tuple(sorted(set((*segment.redactions, *cleaned.redactions)))),
                )
            )
    ordered_segments = tuple(ordered_segments_list)
    estimated_words = sum(len(segment.spoken_text.split()) for segment in ordered_segments)
    estimated_duration_ms = (
        round(estimated_words / config.selection.words_per_minute * 60_000)
        + max(0, len(ordered_segments) - 1) * config.speech.segment_pause_ms
    )
    revised = replace(
        current,
        segments=ordered_segments,
        revision=current.revision + 1,
        estimated_words=estimated_words,
        estimated_duration_ms=estimated_duration_ms,
        warnings=tuple(
            dict.fromkeys((*current.warnings, "Narration edited by a human in the browser."))
        ),
    )
    storage.write_script(run_id, revised, render_script_report(revised))
    storage.set_stage(
        run_id,
        "script",
        "completed",
        input_hash=json_hash({"human_script": revised.to_dict()}),
    )
    storage.mark_stages_stale(
        run_id,
        ("synthesize", "caption", *RENDER_STAGES),
        reason="Human script changed; regenerate downstream artifacts.",
    )
    return revised


def select(
    run_id: str,
    config: AppConfig,
    storage: RunStorage,
) -> SelectionResult:
    """Select complete, explainable comment candidates for an existing run."""
    snapshot = storage.read_thread(run_id)
    existing_selection = (
        storage.read_selection(run_id)
        if storage.artifact_path(run_id, "selection.json").is_file()
        else None
    )
    if (
        existing_selection is not None
        and existing_selection.manually_edited
        and storage.stage_statuses(run_id).get("select") == "completed"
        and storage.artifacts_are_current(run_id, ("selection",))
    ):
        return existing_selection
    input_hash = json_hash(
        {
            "thread": snapshot.to_dict(),
            "selection": config.public_dict()["selection"],
        }
    )
    if storage.stage_is_current(run_id, "select", input_hash, ("selection",)):
        return storage.read_selection(run_id)
    storage.set_stage(run_id, "select", "running", input_hash=input_hash)
    try:
        result = select_thread(snapshot, config.selection)
        storage.write_selection(run_id, result)
        storage.set_stage(run_id, "select", "completed", input_hash=input_hash)
        storage.mark_stages_stale(
            run_id,
            ("script", "synthesize", "caption", *RENDER_STAGES),
            reason="Selection changed; regenerate downstream artifacts.",
        )
    except StorySurferError as exc:
        storage.set_stage(
            run_id,
            "select",
            "failed",
            message=exc.message,
            input_hash=input_hash,
        )
        raise
    except Exception:
        storage.set_stage(
            run_id,
            "select",
            "failed",
            message="Unexpected selection failure.",
            input_hash=input_hash,
        )
        raise
    return result


def script_run(run_id: str, config: AppConfig, storage: RunStorage) -> NarrationScript:
    """Build and persist a human-reviewable, source-linked narration script."""
    snapshot = storage.read_thread(run_id)
    selection = storage.read_selection(run_id)
    existing_script = (
        storage.read_script(run_id)
        if storage.artifact_path(run_id, "script.json").is_file()
        else None
    )
    if (
        existing_script is not None
        and existing_script.revision > 1
        and storage.stage_statuses(run_id).get("script") == "completed"
        and storage.artifacts_are_current(run_id, ("script", "script_report"))
    ):
        return existing_script
    input_hash = json_hash(
        {
            "thread": snapshot.to_dict(),
            "selection": selection.to_dict(),
            "pronunciations": [list(item) for item in config.speech.pronunciations],
            "speech_cleaner_version": SPEECH_CLEANER_VERSION,
            "words_per_minute": config.selection.words_per_minute,
            "segment_pause_ms": config.speech.segment_pause_ms,
        }
    )
    if storage.stage_is_current(run_id, "script", input_hash, ("script", "script_report")):
        return storage.read_script(run_id)
    storage.set_stage(run_id, "script", "running", input_hash=input_hash)
    try:
        script = build_narration_script(
            snapshot,
            selection,
            config.selection,
            config.speech,
        )
        storage.write_script(run_id, script, render_script_report(script))
        storage.set_stage(run_id, "script", "completed", input_hash=input_hash)
        storage.mark_stages_stale(
            run_id,
            ("synthesize", "caption", *RENDER_STAGES),
            reason="Narration script changed; regenerate downstream artifacts.",
        )
    except StorySurferError as exc:
        storage.set_stage(
            run_id,
            "script",
            "failed",
            message=exc.message,
            input_hash=input_hash,
        )
        raise
    except Exception:
        storage.set_stage(
            run_id,
            "script",
            "failed",
            message="Unexpected script generation failure.",
            input_hash=input_hash,
        )
        raise
    return script


def narrate(
    run_id: str,
    config: AppConfig,
    storage: RunStorage,
    *,
    speech_factory: SpeechFactory | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> SpeechArtifact:
    """Synthesize cached segment speech and compose absolute word timing."""
    script = storage.read_script(run_id)
    speech_public = config.public_dict()["speech"]
    input_hash = json_hash(
        {"script_content_hash": _script_content_hash(script), "speech": speech_public}
    )
    if storage.stage_is_current(run_id, "synthesize", input_hash, ("speech", "narration_audio")):
        return storage.read_speech(run_id)
    storage.set_stage(run_id, "synthesize", "running", input_hash=input_hash)
    try:
        factory = speech_factory or _edge_speech
        artifact = synthesize_script(
            run_id,
            script,
            factory(config),
            config.speech,
            storage,
            cancel_check=cancel_check,
        )
        storage.set_stage(run_id, "synthesize", "completed", input_hash=input_hash)
        storage.mark_stages_stale(
            run_id,
            ("caption", *RENDER_STAGES),
            reason="Narration audio or timing changed; regenerate downstream artifacts.",
        )
    except StorySurferError as exc:
        storage.set_stage(
            run_id,
            "synthesize",
            "failed",
            message=exc.message,
            input_hash=input_hash,
        )
        raise
    except Exception:
        storage.set_stage(
            run_id,
            "synthesize",
            "failed",
            message="Unexpected speech synthesis failure.",
            input_hash=input_hash,
        )
        raise
    return artifact


def caption_run(
    run_id: str,
    config: AppConfig,
    storage: RunStorage,
) -> CaptionArtifact:
    """Generate inspectable ASS and SRT captions from measured narration timing."""
    script = storage.read_script(run_id)
    speech = storage.read_speech(run_id)
    caption_public = config.public_dict()["captions"]
    input_hash = json_hash(
        {
            "script_content_hash": _script_content_hash(script),
            "speech": speech.to_dict(),
            "captions": caption_public,
            "layout": {
                "output_width": config.media.output_width,
                "output_height": config.media.output_height,
            },
        }
    )
    if storage.stage_is_current(
        run_id,
        "caption",
        input_hash,
        ("captions", "captions_ass", "captions_srt"),
    ):
        return storage.read_captions(run_id)
    storage.set_stage(run_id, "caption", "running", input_hash=input_hash)
    try:
        artifact = build_caption_artifact(script, speech, config.captions)
        storage.write_text(
            run_id,
            artifact.ass_path,
            render_ass(artifact, config.captions, config.media),
        )
        storage.write_text(run_id, artifact.srt_path, render_srt(artifact))
        storage.write_captions(run_id, artifact)
        storage.set_stage(run_id, "caption", "completed", input_hash=input_hash)
        storage.mark_stages_stale(
            run_id,
            RENDER_STAGES,
            reason="Caption timing or style changed; regenerate downstream artifacts.",
        )
    except StorySurferError as exc:
        storage.set_stage(run_id, "caption", "failed", message=exc.message, input_hash=input_hash)
        raise
    except Exception:
        storage.set_stage(
            run_id,
            "caption",
            "failed",
            message="Unexpected caption generation failure.",
            input_hash=input_hash,
        )
        raise
    return artifact


def preview(
    run_id: str,
    background_path: Path,
    preset: Literal["subway", "minecraft"],
    config: AppConfig,
    storage: RunStorage,
    *,
    crop_offset: float = 0.0,
    renderer: Renderer | None = None,
) -> Timeline:
    """Render or reuse the low-resolution preview profile."""
    return render_run(
        run_id,
        background_path,
        preset,
        config,
        storage,
        profile="preview",
        crop_offset=crop_offset,
        renderer=renderer,
    )


def render_final(
    run_id: str,
    background_path: Path,
    preset: Literal["subway", "minecraft"],
    config: AppConfig,
    storage: RunStorage,
    *,
    acknowledge_rights: bool,
    crop_offset: float = 0.0,
    renderer: Renderer | None = None,
) -> Timeline:
    """Render or reuse final.mp4 after an explicit rights acknowledgement."""
    return render_run(
        run_id,
        background_path,
        preset,
        config,
        storage,
        profile="final",
        acknowledge_rights=acknowledge_rights,
        crop_offset=crop_offset,
        renderer=renderer,
    )


def render_run(
    run_id: str,
    background_path: Path,
    preset: Literal["subway", "minecraft"],
    config: AppConfig,
    storage: RunStorage,
    *,
    profile: RenderProfile,
    acknowledge_rights: bool = False,
    crop_offset: float = 0.0,
    renderer: Renderer | None = None,
) -> Timeline:
    if profile == "final" and not acknowledge_rights:
        raise RightsError(
            "Final rendering requires acknowledgement that the gameplay, music, fonts, "
            "Reddit content, and output use are permitted.",
            hint="Review the sources and assets, then pass --acknowledge-rights.",
        )
    speech = storage.read_speech(run_id)
    captions = storage.read_captions(run_id)
    background = probe_media(background_path, config.media.ffprobe_path)
    effective_media = media_for_profile(config.media, profile)
    stage = f"render_{profile}"
    timeline_name = f"timeline-{profile}.json"
    timeline_key = f"timeline_{profile}"
    output_name = f"{profile}.mp4"
    input_hash = json_hash(
        {
            "speech": speech.to_dict(),
            "captions": captions.to_dict(),
            "background_sha256": _required_file_hash(background.path, "gameplay"),
            "preset": preset,
            "crop_offset": crop_offset,
            "profile": profile,
            "rights_acknowledged": acknowledge_rights,
            "media": _render_settings(effective_media),
        }
    )
    if storage.stage_is_current(run_id, stage, input_hash, (timeline_key, profile)):
        return storage.read_timeline(run_id, name=timeline_name)
    storage.set_stage(run_id, stage, "running", input_hash=input_hash)
    try:
        timeline = build_timeline(
            speech,
            captions,
            background,
            effective_media,
            preset=preset,
            crop_offset=crop_offset,
            profile=profile,
            rights_acknowledged=acknowledge_rights,
        )
        storage.write_timeline(
            run_id,
            timeline,
            name=timeline_name,
            artifact_key=timeline_key,
        )
        selected_renderer = renderer or _render_video
        selected_renderer(storage.run_dir(run_id), timeline, effective_media, output_name)
        storage.record_artifact(run_id, profile, output_name)
        storage.set_stage(run_id, stage, "completed", input_hash=input_hash)
        storage.mark_stages_stale(
            run_id,
            (f"verify_{profile}",),
            reason=f"{profile.title()} video changed; verify it again.",
        )
    except StorySurferError as exc:
        storage.set_stage(run_id, stage, "failed", message=exc.message, input_hash=input_hash)
        raise
    except Exception:
        storage.set_stage(
            run_id,
            stage,
            "failed",
            message=f"Unexpected {profile} render failure.",
            input_hash=input_hash,
        )
        raise
    return timeline


def verify(
    run_id: str,
    profile: RenderProfile,
    config: AppConfig,
    storage: RunStorage,
) -> VerificationReport:
    """Verify streams, timing, dimensions, captions, rights, and artifact integrity."""
    timeline_name = f"timeline-{profile}.json"
    timeline_key = f"timeline_{profile}"
    output_name = f"{profile}.mp4"
    report_name = "verification.json" if profile == "final" else "verification-preview.json"
    report_key = f"verification_{profile}"
    stage = f"verify_{profile}"
    timeline = storage.read_timeline(run_id, name=timeline_name)
    captions = storage.read_captions(run_id)
    output = storage.artifact_path(run_id, output_name)
    required_artifacts = (
        "thread",
        "selection",
        "script",
        "script_report",
        "speech",
        "narration_audio",
        "captions",
        "captions_ass",
        "captions_srt",
        timeline_key,
        profile,
    )
    artifact_integrity = (
        storage.artifacts_are_current(run_id, required_artifacts)
        and storage.stage_statuses(run_id).get(f"render_{profile}") == "completed"
    )
    input_hash = json_hash(
        {
            "timeline": timeline.to_dict(),
            "captions": captions.to_dict(),
            "output_sha256": _required_file_hash(output, f"{profile} video"),
            "duration_tolerance_ms": config.media.duration_tolerance_ms,
            "artifact_integrity": artifact_integrity,
        }
    )
    if storage.stage_is_current(run_id, stage, input_hash, (report_key,)):
        return storage.read_verification(run_id, name=report_name)
    storage.set_stage(run_id, stage, "running", input_hash=input_hash)
    try:
        report = verify_rendered_media(
            output,
            timeline,
            captions,
            config.media,
            profile=profile,
            artifact_integrity=artifact_integrity,
        )
        storage.write_verification(run_id, report, name=report_name, artifact_key=report_key)
        if not report.passed:
            failed = ", ".join(check.name for check in report.checks if not check.passed)
            raise VerificationError(
                f"{profile.title()} verification failed: {failed}",
                hint=f"Inspect {report_name}, correct the inputs or settings, and retry.",
            )
        storage.set_stage(run_id, stage, "completed", input_hash=input_hash)
        return report
    except StorySurferError as exc:
        storage.set_stage(run_id, stage, "failed", message=exc.message, input_hash=input_hash)
        raise
    except Exception:
        storage.set_stage(
            run_id,
            stage,
            "failed",
            message=f"Unexpected {profile} verification failure.",
            input_hash=input_hash,
        )
        raise


def build(
    background_path: Path,
    preset: Literal["subway", "minecraft"],
    config: AppConfig,
    storage: RunStorage,
    *,
    source_value: str | None = None,
    cached_thread: ThreadSnapshot | None = None,
    resume_run_id: str | None = None,
    profile: RenderProfile = "preview",
    acknowledge_rights: bool = False,
    crop_offset: float = 0.0,
    source_factory: SourceFactory | None = None,
    speech_factory: SpeechFactory | None = None,
    renderer: Renderer | None = None,
) -> BuildResult:
    """Run all required stages, reusing only hash-valid completed artifacts."""
    if profile == "final" and not acknowledge_rights:
        raise RightsError(
            "Final builds require --acknowledge-rights before any network or render work."
        )
    supplied_inputs = sum(
        value is not None for value in (source_value, cached_thread, resume_run_id)
    )
    if supplied_inputs != 1:
        raise ConfigurationError(
            "Build requires exactly one Reddit source, cached thread, or resume run ID."
        )
    if resume_run_id is not None:
        storage.require_run(resume_run_id)
        run_id = resume_run_id
    elif cached_thread is not None:
        run_id, _ = ingest_snapshot(cached_thread, config, storage)
    else:
        assert source_value is not None
        run_id, _ = ingest(source_value, config, storage, source_factory=source_factory)

    try:
        select(run_id, config, storage)
        script_run(run_id, config, storage)
        narrate(run_id, config, storage, speech_factory=speech_factory)
        caption_run(run_id, config, storage)
        render_run(
            run_id,
            background_path,
            preset,
            config,
            storage,
            profile=profile,
            acknowledge_rights=acknowledge_rights,
            crop_offset=crop_offset,
            renderer=renderer,
        )
        report = verify(run_id, profile, config, storage)
    except StorySurferError as exc:
        if exc.hint is None:
            exc.hint = (
                f"Run {run_id} was preserved. Resume with: "
                f"storysurfer build --resume {run_id} --background {background_path}"
            )
        raise
    return BuildResult(run_id=run_id, profile=profile, verification=report)


def _praw_source(config: AppConfig) -> RedditThreadSource:
    return PrawRedditClient(config.reddit)


def _edge_speech(config: AppConfig) -> SpeechProvider:
    return EdgeTTSSpeechProvider(config.speech)


def _render_video(
    run_dir: Path,
    timeline: Timeline,
    media: MediaConfig,
    output_name: str,
) -> Path:
    return render_video(run_dir, timeline, media, output_name=output_name)


def _script_content_hash(script: NarrationScript) -> str:
    return json_hash(
        {
            "thread_id": script.thread_id,
            "target_duration_seconds": script.target_duration_seconds,
            "estimated_duration_ms": script.estimated_duration_ms,
            "estimated_words": script.estimated_words,
            "revision": script.revision,
            "segments": [segment.to_dict() for segment in script.segments],
            "warnings": list(script.warnings),
        }
    )


def _required_file_hash(path: Path, label: str) -> str:
    try:
        return file_hash(path)
    except OSError as exc:
        raise MediaError(f"Could not read {label}: {path}") from exc


def _render_settings(media: MediaConfig) -> dict[str, JsonValue]:
    return {
        "ffmpeg_path": media.ffmpeg_path,
        "output_width": media.output_width,
        "output_height": media.output_height,
        "frame_rate": media.frame_rate,
        "video_codec": media.video_codec,
        "audio_codec": media.audio_codec,
        "crf": media.crf,
        "encoder_preset": media.encoder_preset,
        "retain_background_audio": media.retain_background_audio,
        "background_volume": media.background_volume,
    }


def _reviewed_text(value: str) -> str:
    result = " ".join(value.split())
    if not result:
        raise ScriptError("Narration segments cannot be empty.")
    if len(result) > 20_000:
        raise ScriptError("Narration segment exceeds the browser editing limit.")
    return result
