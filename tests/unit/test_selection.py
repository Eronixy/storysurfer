from __future__ import annotations

from dataclasses import replace

from storysurfer.config import SelectionConfig
from storysurfer.domain import Comment, SelectionResult, ThreadSnapshot
from storysurfer.editorial.select import select_thread


def test_direct_op_reply_is_atomic_and_keeps_parent_first(
    thread_snapshot: ThreadSnapshot,
) -> None:
    result = select_thread(
        thread_snapshot,
        SelectionConfig(target_duration_seconds=120, max_selected_candidates=5),
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
        SelectionConfig(target_duration_seconds=120, max_selected_candidates=10),
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
