from __future__ import annotations

from dataclasses import replace

import pytest

from storysurfer.config import SelectionConfig, SpeechConfig
from storysurfer.domain import Comment, SelectionResult, ThreadSnapshot
from storysurfer.editorial.script import build_narration_script
from storysurfer.editorial.select import select_thread
from storysurfer.errors import SourceError
from storysurfer.pipeline import _with_update_posts


def test_direct_op_reply_is_atomic_and_keeps_parent_first(
    thread_snapshot: ThreadSnapshot,
) -> None:
    result = select_thread(
        thread_snapshot,
        SelectionConfig(target_duration_seconds=120),
    )
    exchange = next(candidate for candidate in result.candidates if candidate.kind == "op_exchange")

    assert exchange.source_ids == ("c1", "o1")
    assert exchange.selected
    assert "direct_op_reply" in exchange.reason_codes
    assert "parent_context_included" in exchange.reason_codes


def test_deleted_bots_and_duplicate_comment_are_not_selected(
    thread_snapshot: ThreadSnapshot,
) -> None:
    result = select_thread(
        thread_snapshot,
        SelectionConfig(
            target_duration_seconds=120,
            requested_op_exchanges=10,
            requested_comments=10,
        ),
    )
    source_ids = {source for candidate in result.candidates for source in candidate.source_ids}
    duplicate = next(
        candidate for candidate in result.candidates if candidate.source_ids == ("c3",)
    )

    assert "deleted1" not in source_ids
    assert "bot1" not in source_ids
    assert "duplicate_candidate" in duplicate.reason_codes
    assert not duplicate.selected


def test_selection_is_deterministic_and_round_trips(thread_snapshot: ThreadSnapshot) -> None:
    config = SelectionConfig(target_duration_seconds=120)
    first = select_thread(thread_snapshot, config)
    second = select_thread(thread_snapshot, config)

    assert first == second
    assert SelectionResult.from_dict(first.to_dict()) == first


def test_complete_candidate_is_not_cut_to_fill_budget(thread_snapshot: ThreadSnapshot) -> None:
    result = select_thread(
        thread_snapshot,
        SelectionConfig(
            target_duration_seconds=15,
            words_per_minute=80,
            comment_budget_fraction=0.1,
        ),
    )

    assert result.comment_word_budget == 2
    assert result.selected_comment_words == 0
    assert not any(candidate.selected for candidate in result.candidates)


def test_generic_top_level_op_reaction_is_not_mislabeled_as_update(
    thread_snapshot: ThreadSnapshot,
) -> None:
    generic_op = Comment(
        id="opthanks",
        parent_id=thread_snapshot.submission.id,
        author_id=thread_snapshot.submission.author_id,
        body="Thank you all for reading and responding to the story.",
        score=900,
        depth=0,
        order=99,
        permalink="https://www.reddit.com/comments/p12345/opthanks/",
        is_op=True,
    )
    snapshot = replace(
        thread_snapshot,
        comments=(*thread_snapshot.comments, generic_op),
    )

    result = select_thread(snapshot, SelectionConfig(target_duration_seconds=120))

    assert all("opthanks" not in candidate.source_ids for candidate in result.candidates)


def test_requested_comment_and_exchange_counts_are_independent(
    thread_snapshot: ThreadSnapshot,
) -> None:
    result = select_thread(
        thread_snapshot,
        SelectionConfig(
            target_duration_seconds=600,
            requested_op_exchanges=1,
            requested_comments=0,
        ),
    )
    selected = [candidate for candidate in result.candidates if candidate.selected]

    assert sum(candidate.kind == "op_exchange" for candidate in selected) <= 1
    assert not any(candidate.kind == "comment" for candidate in selected)


def test_linked_update_post_is_verified_and_selected_outside_discussion_quotas(
    thread_snapshot: ThreadSnapshot,
) -> None:
    update = replace(
        thread_snapshot,
        submission=replace(
            thread_snapshot.submission,
            id="update1",
            title="What happened next",
            body="We talked and resolved the situation yesterday.",
            permalink="https://www.reddit.com/comments/update1/",
        ),
        comments=(),
    )

    merged = _with_update_posts(thread_snapshot, (update,))
    config = SelectionConfig(
        target_duration_seconds=15,
        words_per_minute=80,
        comment_budget_fraction=0.1,
        requested_op_exchanges=0,
        requested_comments=0,
    )
    result = select_thread(
        merged,
        config,
    )
    linked = next(
        candidate for candidate in result.candidates if candidate.source_ids == ("update-update1",)
    )
    script = build_narration_script(merged, result, config, SpeechConfig())
    update_segment = next(segment for segment in script.segments if segment.kind == "op_update")

    assert linked.kind == "op_update"
    assert linked.selected
    assert merged.comments[-1].permalink == update.submission.permalink
    assert update_segment.source_refs[0].source_type == "post"
    assert update_segment.source_refs[0].source_id == "update1"
    assert update_segment.source_refs[0].permalink == update.submission.permalink


def test_update_post_must_match_main_post_author(thread_snapshot: ThreadSnapshot) -> None:
    update = replace(
        thread_snapshot,
        submission=replace(
            thread_snapshot.submission,
            id="update2",
            author_id="anon:different",
        ),
        comments=(),
    )

    with pytest.raises(SourceError, match="not written by"):
        _with_update_posts(thread_snapshot, (update,))
