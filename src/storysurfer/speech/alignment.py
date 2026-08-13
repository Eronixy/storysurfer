"""Convert and validate provider timing data."""

from __future__ import annotations

import re
from typing import cast

from storysurfer.errors import AlignmentError
from storysurfer.speech.base import RelativeWord, SegmentSpeech

NON_WHITESPACE = re.compile(r"\S+")


def words_from_character_alignment(value: object) -> tuple[RelativeWord, ...]:
    if not isinstance(value, dict):
        raise AlignmentError("Speech provider returned no character alignment.")
    data = cast(dict[object, object], value)
    characters = data.get("characters")
    starts = data.get("character_start_times_seconds")
    ends = data.get("character_end_times_seconds")
    if not isinstance(characters, list) or not all(isinstance(item, str) for item in characters):
        raise AlignmentError("Speech character alignment contains invalid characters.")
    if not isinstance(starts, list) or not isinstance(ends, list):
        raise AlignmentError("Speech character alignment contains invalid timing arrays.")
    if len(characters) != len(starts) or len(characters) != len(ends) or not characters:
        raise AlignmentError("Speech character alignment arrays have different lengths.")
    numeric_starts = _numbers(starts, "start")
    numeric_ends = _numbers(ends, "end")
    text = "".join(characters)
    words: list[RelativeWord] = []
    for match in NON_WHITESPACE.finditer(text):
        start_index = match.start()
        end_index = match.end() - 1
        # Providers document one list item per character; reject chunked alignment explicitly.
        if len(text) != len(characters):
            raise AlignmentError(
                "Speech provider returned chunked rather than character alignment."
            )
        words.append(
            RelativeWord(
                text=match.group(0),
                start_ms=round(numeric_starts[start_index] * 1_000),
                end_ms=round(numeric_ends[end_index] * 1_000),
            )
        )
    if not words:
        raise AlignmentError("Speech provider returned alignment without any words.")
    return tuple(words)


def validate_segment_speech(speech: SegmentSpeech) -> None:
    if speech.sample_rate <= 0:
        raise AlignmentError("Speech sample rate must be positive.")
    if not speech.pcm_s16le or len(speech.pcm_s16le) % 2:
        raise AlignmentError("Speech audio must be non-empty signed 16-bit PCM.")
    if not speech.words:
        raise AlignmentError("Speech provider returned no word timings.")
    previous_end = 0
    for word in speech.words:
        if not word.text.strip():
            raise AlignmentError("Speech alignment contains an empty word.")
        if word.start_ms < 0 or word.end_ms <= word.start_ms:
            raise AlignmentError("Speech word timing is negative or has no duration.")
        if word.start_ms < previous_end:
            raise AlignmentError("Speech word timings overlap or are not monotonic.")
        previous_end = word.end_ms
    if speech.words[-1].end_ms > speech.duration_ms + 100:
        raise AlignmentError("Speech alignment extends beyond the returned audio duration.")


def _numbers(values: list[object], label: str) -> list[float]:
    result: list[float] = []
    for value in values:
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise AlignmentError(f"Speech character {label} timing is not numeric.")
        number = float(value)
        if number < 0:
            raise AlignmentError(f"Speech character {label} timing is negative.")
        result.append(number)
    return result
