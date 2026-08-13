"""Pure caption phrase construction from measured speech word timing."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from storysurfer.config import CaptionConfig
from storysurfer.domain import (
    CaptionArtifact,
    CaptionCue,
    CaptionWord,
    NarrationScript,
    NarrationSegment,
    SpeechArtifact,
    SpokenWord,
)
from storysurfer.errors import CaptionError

BREAK_PUNCTUATION = re.compile(r"[.!?](?:[\"')\]]+)?$")


def build_caption_artifact(
    script: NarrationScript,
    speech: SpeechArtifact,
    config: CaptionConfig,
) -> CaptionArtifact:
    """Chunk actual TTS word timings into short, deterministic caption phrases."""
    if config.min_words > config.max_words:
        raise CaptionError("captions.min_words cannot exceed captions.max_words.")
    if speech.duration_ms <= 0 or not speech.words:
        raise CaptionError("Narration has no usable duration or word timings.")

    segments = {segment.id: segment for segment in script.segments}
    groups: list[list[SpokenWord]] = []
    current: list[SpokenWord] = []
    previous: SpokenWord | None = None
    for word in speech.words:
        if word.segment_id not in segments:
            raise CaptionError(
                f"Speech word refers to unknown narration segment: {word.segment_id}"
            )
        if word.start_ms < 0 or word.end_ms <= word.start_ms:
            raise CaptionError("Speech contains a negative or empty word timestamp.")
        if previous is not None and word.start_ms < previous.end_ms:
            raise CaptionError("Speech word timestamps overlap or are not monotonic.")

        boundary = bool(
            current
            and previous is not None
            and (
                word.segment_id != previous.segment_id
                or word.start_ms - previous.end_ms >= config.gap_break_ms
                or len(current) >= config.max_words
                or _character_count((*current, word)) > config.max_characters
            )
        )
        if boundary:
            groups.append(current)
            current = []
        current.append(word)
        if len(current) >= config.min_words and BREAK_PUNCTUATION.search(word.text):
            groups.append(current)
            current = []
        previous = word
    if current:
        groups.append(current)

    groups = _merge_short_groups(groups, config)
    cues: list[CaptionCue] = []
    for index, words in enumerate(groups):
        segment = segments[words[0].segment_id]
        next_start = groups[index + 1][0].start_ms if index + 1 < len(groups) else None
        end_ms = min(speech.duration_ms, words[-1].end_ms + config.tail_ms)
        if next_start is not None:
            end_ms = min(end_ms, next_start)
        end_ms = max(words[-1].end_ms, end_ms)
        cue_id = _cue_id(segment.id, words)
        cues.append(
            CaptionCue(
                id=cue_id,
                text=_wrap_phrase(words, config.max_characters),
                start_ms=words[0].start_ms,
                end_ms=end_ms,
                segment_id=segment.id,
                speaker_label=segment.speaker_label,
                style=_style(segment),
                words=tuple(
                    CaptionWord(text=word.text, start_ms=word.start_ms, end_ms=word.end_ms)
                    for word in words
                ),
            )
        )
    _validate_cues(cues, speech.duration_ms)
    return CaptionArtifact(
        duration_ms=speech.duration_ms,
        ass_path="captions.ass",
        srt_path="captions.srt",
        cues=tuple(cues),
    )


def _merge_short_groups(
    groups: list[list[SpokenWord]], config: CaptionConfig
) -> list[list[SpokenWord]]:
    if len(groups) < 2:
        return groups
    merged: list[list[SpokenWord]] = []
    for group in groups:
        if (
            len(group) < config.min_words
            and merged
            and merged[-1][0].segment_id == group[0].segment_id
            and len(merged[-1]) + len(group) <= config.max_words
            and _character_count((*merged[-1], *group)) <= config.max_characters
        ):
            merged[-1].extend(group)
        else:
            merged.append(group)
    return merged


def _character_count(words: tuple[SpokenWord, ...]) -> int:
    return len(" ".join(word.text.strip() for word in words))


def _wrap_phrase(words: list[SpokenWord], max_characters: int) -> str:
    tokens = [word.text.strip() for word in words]
    phrase = " ".join(tokens)
    line_limit = max(1, max_characters // 2)
    if len(phrase) <= line_limit or len(tokens) == 1:
        return phrase
    best_split = min(
        range(1, len(tokens)),
        key=lambda index: abs(
            len(" ".join(tokens[:index])) - len(" ".join(tokens[index:]))
        ),
    )
    return f"{' '.join(tokens[:best_split])}\n{' '.join(tokens[best_split:])}"


def _style(segment: NarrationSegment) -> Literal["title", "story", "commenter", "op"]:
    if segment.kind == "title":
        return "title"
    if segment.kind == "comment":
        return "commenter"
    if segment.kind in {"op_reply", "op_update"}:
        return "op"
    return "story"


def _cue_id(segment_id: str, words: list[SpokenWord]) -> str:
    identity = f"{segment_id}:{words[0].start_ms}:{words[-1].end_ms}".encode()
    return f"cue-{hashlib.sha256(identity).hexdigest()[:12]}"


def _validate_cues(cues: list[CaptionCue], duration_ms: int) -> None:
    if not cues:
        raise CaptionError("No caption cues could be generated from narration timing.")
    previous_end = 0
    for cue in cues:
        if cue.start_ms < previous_end or cue.end_ms <= cue.start_ms:
            raise CaptionError("Caption cues overlap or are not monotonic.")
        if cue.end_ms > duration_ms:
            raise CaptionError("Caption cue extends beyond narration duration.")
        if cue.text.count("\n") > 1:
            raise CaptionError("Caption cue exceeds the two-line limit.")
        previous_end = cue.end_ms
