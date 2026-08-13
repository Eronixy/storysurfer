from __future__ import annotations

from datetime import UTC, datetime

import pytest

from storysurfer.errors import SourceError
from storysurfer.reddit.models import RawComment, RawPost, RawThread
from storysurfer.reddit.normalize import normalize_thread


def _raw_thread(*, over_18: bool = False) -> RawThread:
    post = RawPost(
        id="t3_abc123",
        title="Synthetic title",
        body="Synthetic story body",
        author_name="StoryAuthor",
        score=10,
        permalink="/r/synthetic/comments/abc123/story/",
        over_18=over_18,
        locked=False,
        removed_by_category=None,
        quarantined=False,
    )
    comments = (
        RawComment(
            id="t1_reply1",
            parent_id="t3_abc123",
            author_name="StoryAuthor",
            body="A useful update from OP.",
            score=5,
            depth=0,
            order=0,
            permalink="/r/synthetic/comments/abc123/story/reply1/",
            created_utc=100.0,
            is_submitter=True,
            removed_by_category=None,
        ),
        RawComment(
            id="t1_deleted1",
            parent_id="t3_abc123",
            author_name=None,
            body="[deleted]",
            score=20,
            depth=0,
            order=1,
            permalink="/r/synthetic/comments/abc123/story/deleted1/",
            created_utc=101.0,
            is_submitter=False,
            removed_by_category=None,
        ),
    )
    return RawThread(
        source_url="https://www.reddit.com/comments/abc123/", post=post, comments=comments
    )


def test_normalization_hashes_authors_and_marks_removed_content() -> None:
    snapshot = normalize_thread(
        _raw_thread(),
        author_hash_salt="fixture-salt",
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert snapshot.submission.id == "abc123"
    assert snapshot.comments[0].is_op
    assert snapshot.comments[0].author_id == snapshot.submission.author_id
    assert snapshot.comments[1].removed
    assert snapshot.comments[1].body == ""
    assert snapshot.comments[1].author_id is None


def test_nsfw_thread_is_rejected() -> None:
    with pytest.raises(SourceError, match="NSFW"):
        normalize_thread(_raw_thread(over_18=True), author_hash_salt="fixture-salt")
