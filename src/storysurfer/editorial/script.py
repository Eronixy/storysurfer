"""Create a reviewable narration script grounded in selected sources."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Literal

from storysurfer.config import SelectionConfig, SpeechConfig
from storysurfer.domain import (
    Comment,
    NarrationScript,
    NarrationSegment,
    SelectionResult,
    SourceRef,
    ThreadSnapshot,
)
from storysurfer.editorial.clean import CleanedText, clean_for_speech
from storysurfer.editorial.text import word_count
from storysurfer.errors import ScriptError
from storysurfer.storage import utc_now


def build_narration_script(
    snapshot: ThreadSnapshot,
    selection: SelectionResult,
    selection_config: SelectionConfig,
    speech_config: SpeechConfig,
    *,
    now: Callable[[], str] = utc_now,
) -> NarrationScript:
    if selection.thread_id != snapshot.submission.id:
        raise ScriptError("Selection and thread artifacts refer to different submissions.")
    pronunciations = speech_config.pronunciations
    segments: list[NarrationSegment] = []
    warnings: list[str] = list(selection.warnings)

    cleaned_title = clean_for_speech(snapshot.submission.title, pronunciations=pronunciations)
    _require_text(cleaned_title, "submission title")
    segments.append(
        _segment(
            segment_id=f"title-{snapshot.submission.id}",
            kind="title",
            speaker="Narrator",
            prefix="Reddit story: ",
            cleaned=cleaned_title,
            original=snapshot.submission.title,
            source=_post_ref(snapshot, snapshot.submission.title),
            priority=1.0,
        )
    )

    cleaned_body = clean_for_speech(snapshot.submission.body, pronunciations=pronunciations)
    _require_text(cleaned_body, "submission body")
    segments.append(
        _segment(
            segment_id=f"post-{snapshot.submission.id}",
            kind="post",
            speaker="OP",
            prefix="Here is what OP wrote. ",
            cleaned=cleaned_body,
            original=snapshot.submission.body,
            source=_post_ref(snapshot, snapshot.submission.body),
            priority=1.0,
        )
    )

    comments = {comment.id: comment for comment in snapshot.comments}
    for candidate in selection.candidates:
        if not candidate.selected:
            continue
        candidate_comments = _candidate_comments(candidate.source_ids, comments)
        if candidate.kind == "op_exchange":
            if len(candidate_comments) != 2:
                raise ScriptError(f"OP exchange is incomplete: {candidate.id}")
            parent, reply = candidate_comments
            if parent.is_op or not reply.is_op or reply.parent_id != parent.id:
                raise ScriptError(f"OP exchange has invalid source ordering: {candidate.id}")
            segments.append(
                _comment_segment(
                    parent,
                    kind="comment",
                    speaker="Commenter",
                    prefix=(
                        "One commenter asked. " if "?" in parent.body else "One commenter wrote. "
                    ),
                    priority=candidate.score,
                    pronunciations=pronunciations,
                )
            )
            segments.append(
                _comment_segment(
                    reply,
                    kind="op_reply",
                    speaker="OP",
                    prefix="OP replied. ",
                    priority=candidate.score,
                    pronunciations=pronunciations,
                )
            )
        elif candidate.kind == "op_update":
            segments.append(
                _comment_segment(
                    candidate_comments[0],
                    kind="op_update",
                    speaker="OP",
                    prefix="OP added an update. ",
                    priority=candidate.score,
                    pronunciations=pronunciations,
                )
            )
        else:
            segments.append(
                _comment_segment(
                    candidate_comments[0],
                    kind="comment",
                    speaker="Commenter",
                    prefix="One commenter wrote. ",
                    priority=candidate.score,
                    pronunciations=pronunciations,
                )
            )

    for segment in segments:
        if segment.redactions:
            types = ", ".join(segment.redactions)
            warnings.append(f"{segment.id} contains automatic redactions: {types}.")

    estimated_words = sum(word_count(segment.spoken_text) for segment in segments)
    speech_ms = round(estimated_words / selection_config.words_per_minute * 60_000)
    pause_ms = max(0, len(segments) - 1) * speech_config.segment_pause_ms
    return NarrationScript(
        thread_id=snapshot.submission.id,
        created_at=now(),
        target_duration_seconds=selection.target_duration_seconds,
        estimated_duration_ms=speech_ms + pause_ms,
        estimated_words=estimated_words,
        segments=tuple(segments),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def render_script_report(script: NarrationScript) -> str:
    lines = [
        f"StorySurfer narration script - {script.thread_id}",
        f"Revision: {script.revision}",
        f"Estimated duration: {script.estimated_duration_ms / 1000:.1f}s",
        f"Estimated words: {script.estimated_words}",
        "",
    ]
    for index, segment in enumerate(script.segments, start=1):
        sources = ", ".join(source.permalink for source in segment.source_refs)
        lines.extend(
            [
                f"[{index}] {segment.speaker_label} / {segment.kind} / {segment.id}",
                f"Source: {sources}",
                f"Original: {segment.original_excerpt}",
                f"Spoken: {segment.spoken_text}",
                f"Shortened: {'yes' if segment.shortened else 'no'}",
                f"Redactions: {', '.join(segment.redactions) or 'none'}",
                "",
            ]
        )
    if script.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in script.warnings)
        lines.append("")
    return "\n".join(lines)


def _candidate_comments(
    source_ids: tuple[str, ...], comments: dict[str, Comment]
) -> tuple[Comment, ...]:
    try:
        return tuple(comments[source_id] for source_id in source_ids)
    except KeyError as exc:
        raise ScriptError(f"Selected source is missing from the thread: {exc.args[0]}") from exc


def _comment_segment(
    comment: Comment,
    *,
    kind: Literal["comment", "op_reply", "op_update"],
    speaker: str,
    prefix: str,
    priority: float,
    pronunciations: tuple[tuple[str, str], ...],
) -> NarrationSegment:
    cleaned = clean_for_speech(comment.body, pronunciations=pronunciations)
    _require_text(cleaned, f"comment {comment.id}")
    return _segment(
        segment_id=f"{kind}-{comment.id}",
        kind=kind,
        speaker=speaker,
        prefix=prefix,
        cleaned=cleaned,
        original=comment.body,
        source=_comment_ref(comment),
        priority=priority,
    )


def _segment(
    *,
    segment_id: str,
    kind: Literal["title", "post", "comment", "op_reply", "op_update"],
    speaker: str,
    prefix: str,
    cleaned: CleanedText,
    original: str,
    source: SourceRef,
    priority: float,
    shortened: bool = False,
) -> NarrationSegment:
    return NarrationSegment(
        id=segment_id,
        kind=kind,
        speaker_label=speaker,
        spoken_text=f"{prefix}{cleaned.text}".strip(),
        original_excerpt=original,
        source_refs=(source,),
        priority=priority,
        shortened=shortened,
        redactions=cleaned.redactions,
    )


def _post_ref(snapshot: ThreadSnapshot, original: str) -> SourceRef:
    return SourceRef(
        source_type="post",
        source_id=snapshot.submission.id,
        permalink=snapshot.submission.permalink,
        author_role="op",
        original_text_hash=_text_hash(original),
    )


def _comment_ref(comment: Comment) -> SourceRef:
    role: Literal["op", "commenter"] = "op" if comment.is_op else "commenter"
    return SourceRef(
        source_type="comment",
        source_id=comment.id,
        permalink=comment.permalink,
        author_role=role,
        original_text_hash=_text_hash(comment.body),
    )


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _require_text(cleaned: CleanedText, label: str) -> None:
    if not cleaned.text:
        raise ScriptError(f"Source has no speakable text after cleanup: {label}")
