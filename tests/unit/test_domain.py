from __future__ import annotations

from storysurfer.domain import ThreadSnapshot


def test_thread_snapshot_round_trip(thread_snapshot: ThreadSnapshot) -> None:
    assert ThreadSnapshot.from_dict(thread_snapshot.to_dict()) == thread_snapshot


def test_snapshot_contains_no_raw_usernames(thread_snapshot: ThreadSnapshot) -> None:
    serialized = str(thread_snapshot.to_dict())
    assert "u/" not in serialized
    assert all(
        comment.author_id is None or comment.author_id.startswith("anon:")
        for comment in thread_snapshot.comments
    )
