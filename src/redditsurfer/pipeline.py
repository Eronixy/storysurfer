"""Application services shared by current and future interfaces."""

from __future__ import annotations

from collections.abc import Callable

from redditsurfer.config import AppConfig
from redditsurfer.domain import NarrationScript, SelectionResult, SpeechArtifact, ThreadSnapshot
from redditsurfer.editorial import select_thread
from redditsurfer.editorial.script import build_narration_script, render_script_report
from redditsurfer.errors import RedditSurferError
from redditsurfer.reddit import PrawRedditClient, RedditThreadSource
from redditsurfer.reddit.normalize import normalize_thread
from redditsurfer.reddit.url import parse_reddit_reference
from redditsurfer.speech import SpeechProvider, synthesize_script
from redditsurfer.speech.edge import EdgeTTSSpeechProvider
from redditsurfer.storage import RunStorage, json_hash

SourceFactory = Callable[[AppConfig], RedditThreadSource]
SpeechFactory = Callable[[AppConfig], SpeechProvider]


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
    except RedditSurferError as exc:
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


def select(
    run_id: str,
    config: AppConfig,
    storage: RunStorage,
) -> SelectionResult:
    """Select complete, explainable comment candidates for an existing run."""
    snapshot = storage.read_thread(run_id)
    previous = (
        storage.read_selection(run_id)
        if storage.artifact_path(run_id, "selection.json").is_file()
        else None
    )
    input_hash = json_hash(
        {
            "thread": snapshot.to_dict(),
            "selection": config.public_dict()["selection"],
        }
    )
    storage.set_stage(run_id, "select", "running", input_hash=input_hash)
    try:
        result = select_thread(snapshot, config.selection)
        storage.write_selection(run_id, result)
        storage.set_stage(run_id, "select", "completed", input_hash=input_hash)
        if previous is not None and previous != result:
            storage.mark_stages_stale(
                run_id,
                ("script", "synthesize", "caption", "render", "verify"),
                reason="Selection changed; regenerate downstream artifacts.",
            )
    except RedditSurferError as exc:
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
    previous = (
        storage.read_script(run_id)
        if storage.artifact_path(run_id, "script.json").is_file()
        else None
    )
    input_hash = json_hash(
        {
            "thread": snapshot.to_dict(),
            "selection": selection.to_dict(),
            "pronunciations": [list(item) for item in config.speech.pronunciations],
            "words_per_minute": config.selection.words_per_minute,
            "segment_pause_ms": config.speech.segment_pause_ms,
        }
    )
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
        if previous is not None and _script_content_hash(previous) != _script_content_hash(script):
            storage.mark_stages_stale(
                run_id,
                ("synthesize", "caption", "render", "verify"),
                reason="Narration script changed; regenerate downstream artifacts.",
            )
    except RedditSurferError as exc:
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
) -> SpeechArtifact:
    """Synthesize cached segment speech and compose absolute word timing."""
    script = storage.read_script(run_id)
    speech_public = config.public_dict()["speech"]
    input_hash = json_hash({"script": script.to_dict(), "speech": speech_public})
    storage.set_stage(run_id, "synthesize", "running", input_hash=input_hash)
    try:
        factory = speech_factory or _edge_speech
        artifact = synthesize_script(
            run_id,
            script,
            factory(config),
            config.speech,
            storage,
        )
        storage.set_stage(run_id, "synthesize", "completed", input_hash=input_hash)
    except RedditSurferError as exc:
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


def _praw_source(config: AppConfig) -> RedditThreadSource:
    return PrawRedditClient(config.reddit)


def _edge_speech(config: AppConfig) -> SpeechProvider:
    return EdgeTTSSpeechProvider(config.speech)


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
