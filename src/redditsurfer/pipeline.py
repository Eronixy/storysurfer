"""Application services shared by current and future interfaces."""

from __future__ import annotations

from collections.abc import Callable

from redditsurfer.config import AppConfig
from redditsurfer.domain import SelectionResult, ThreadSnapshot
from redditsurfer.editorial import select_thread
from redditsurfer.errors import RedditSurferError
from redditsurfer.reddit import PrawRedditClient, RedditThreadSource
from redditsurfer.reddit.normalize import normalize_thread
from redditsurfer.reddit.url import parse_reddit_reference
from redditsurfer.storage import RunStorage, json_hash

SourceFactory = Callable[[AppConfig], RedditThreadSource]


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


def _praw_source(config: AppConfig) -> RedditThreadSource:
    return PrawRedditClient(config.reddit)
