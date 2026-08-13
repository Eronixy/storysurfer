"""Normalize provider values and pseudonymize author identity."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from redditsurfer.domain import Comment, Post, ThreadSnapshot
from redditsurfer.errors import SourceError
from redditsurfer.reddit.models import RawThread

REMOVED_MARKERS = {"[deleted]", "[removed]"}


def normalize_thread(
    raw: RawThread,
    *,
    author_hash_salt: str,
    now: Callable[[], datetime] | None = None,
    allow_nsfw: bool = False,
) -> ThreadSnapshot:
    """Convert a raw provider response into the versioned domain snapshot."""
    if raw.post.over_18 and not allow_nsfw:
        raise SourceError("NSFW submissions are excluded by the current content policy.")
    if raw.post.quarantined:
        raise SourceError("Quarantined submissions are excluded by the current content policy.")
    if _is_removed(raw.post.body, raw.post.removed_by_category):
        raise SourceError("The Reddit submission body was deleted or removed.")

    op_author_id = hash_author(raw.post.author_name, author_hash_salt)
    post = Post(
        id=_bare_id(raw.post.id),
        title=raw.post.title.strip(),
        body=raw.post.body.strip(),
        author_id=op_author_id,
        score=raw.post.score,
        permalink=_absolute_permalink(raw.post.permalink),
        nsfw=raw.post.over_18,
        locked=raw.post.locked,
        removed=False,
    )
    comments: list[Comment] = []
    for raw_comment in raw.comments:
        author_id = hash_author(raw_comment.author_name, author_hash_salt)
        removed = _is_removed(raw_comment.body, raw_comment.removed_by_category)
        author_name = (raw_comment.author_name or "").casefold()
        comments.append(
            Comment(
                id=_bare_id(raw_comment.id),
                parent_id=_bare_id(raw_comment.parent_id),
                author_id=author_id,
                body="" if removed else raw_comment.body.strip(),
                score=raw_comment.score,
                depth=max(0, raw_comment.depth),
                order=raw_comment.order,
                permalink=_absolute_permalink(raw_comment.permalink),
                created_utc=raw_comment.created_utc,
                is_op=raw_comment.is_submitter
                or (author_id is not None and author_id == op_author_id),
                is_bot=bool(author_name) and author_name.endswith("bot"),
                removed=removed,
            )
        )

    current_time = now() if now is not None else datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    return ThreadSnapshot(
        submission=post,
        comments=tuple(comments),
        retrieved_at=current_time.astimezone(UTC).isoformat(),
        source_url=raw.source_url,
    )


def hash_author(author_name: str | None, salt: str) -> str | None:
    if not author_name:
        return None
    digest = hashlib.sha256(f"{salt}\0{author_name.casefold()}".encode()).hexdigest()
    return f"anon:{digest[:16]}"


def _bare_id(value: str) -> str:
    stripped = value.strip()
    if "_" in stripped:
        prefix, remainder = stripped.split("_", 1)
        if prefix in {"t1", "t3"} and remainder:
            return remainder
    return stripped


def _absolute_permalink(permalink: str) -> str:
    if permalink.startswith("http://") or permalink.startswith("https://"):
        return permalink
    return f"https://www.reddit.com{permalink}"


def _is_removed(body: str, removed_by_category: str | None) -> bool:
    return removed_by_category is not None or body.strip().casefold() in REMOVED_MARKERS
