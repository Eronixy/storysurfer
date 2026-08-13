from __future__ import annotations

from storysurfer.config import SelectionConfig, SpeechConfig
from storysurfer.domain import NarrationScript, ThreadSnapshot
from storysurfer.editorial.script import build_narration_script, render_script_report
from storysurfer.editorial.select import select_thread


def test_script_is_source_linked_and_preserves_op_exchange_order(
    thread_snapshot: ThreadSnapshot,
) -> None:
    selection_config = SelectionConfig(target_duration_seconds=120)
    selection = select_thread(thread_snapshot, selection_config)
    script = build_narration_script(
        thread_snapshot,
        selection,
        selection_config,
        SpeechConfig(),
        now=lambda: "2026-01-02T00:00:00+00:00",
    )

    assert script.created_at == "2026-01-02T00:00:00+00:00"
    assert all(segment.source_refs for segment in script.segments)
    assert all(
        source.original_text_hash for segment in script.segments for source in segment.source_refs
    )
    commenter_index = next(
        index for index, segment in enumerate(script.segments) if segment.id == "comment-c1"
    )
    reply_index = next(
        index for index, segment in enumerate(script.segments) if segment.id == "op_reply-o1"
    )
    assert reply_index == commenter_index + 1
    assert script.segments[commenter_index].speaker_label == "Commenter"
    assert script.segments[reply_index].speaker_label == "OP"
    assert NarrationScript.from_dict(script.to_dict()) == script


def test_script_report_compares_original_and_spoken_text(
    thread_snapshot: ThreadSnapshot,
) -> None:
    selection_config = SelectionConfig(target_duration_seconds=120)
    script = build_narration_script(
        thread_snapshot,
        select_thread(thread_snapshot, selection_config),
        selection_config,
        SpeechConfig(),
    )

    report = render_script_report(script)

    assert "Original:" in report
    assert "Spoken:" in report
    assert "Source: https://www.reddit.com/" in report
    assert "OP replied." in report


def test_post_body_is_not_shortened_to_fit_duration_budget(
    thread_snapshot: ThreadSnapshot,
) -> None:
    selection_config = SelectionConfig(target_duration_seconds=15)
    selection = select_thread(thread_snapshot, selection_config)

    script = build_narration_script(
        thread_snapshot,
        selection,
        selection_config,
        SpeechConfig(),
    )

    post = next(segment for segment in script.segments if segment.kind == "post")
    assert post.spoken_text.endswith(thread_snapshot.submission.body)
    assert post.original_excerpt == thread_snapshot.submission.body
    assert not post.shortened
    assert any("complete post exceeds" in warning for warning in script.warnings)
