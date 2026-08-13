from __future__ import annotations

import pytest

from storysurfer.errors import AlignmentError
from storysurfer.speech.alignment import (
    normalize_segment_speech,
    validate_segment_speech,
    words_from_character_alignment,
)
from storysurfer.speech.base import RelativeWord, SegmentSpeech


def test_character_alignment_becomes_word_timing() -> None:
    characters = list("Hello world!")
    alignment = {
        "characters": characters,
        "character_start_times_seconds": [index * 0.05 for index in range(len(characters))],
        "character_end_times_seconds": [(index + 1) * 0.05 for index in range(len(characters))],
    }

    words = words_from_character_alignment(alignment)

    assert [word.text for word in words] == ["Hello", "world!"]
    assert words[0].start_ms == 0
    assert words[0].end_ms == 250
    assert words[1].start_ms == 300
    assert words[1].end_ms == 600


def test_mismatched_character_timing_is_rejected() -> None:
    with pytest.raises(AlignmentError, match="different lengths"):
        words_from_character_alignment(
            {
                "characters": ["H", "i"],
                "character_start_times_seconds": [0.0],
                "character_end_times_seconds": [0.1, 0.2],
            }
        )


def test_small_final_boundary_overrun_is_clamped_to_audio() -> None:
    speech = SegmentSpeech(
        pcm_s16le=b"\x00\x00" * 24_000,
        sample_rate=24_000,
        words=(RelativeWord("word", 900, 1_050),),
    )

    normalized = normalize_segment_speech(speech)

    assert normalized.words[-1].end_ms == 1_000
    validate_segment_speech(normalized)


def test_large_alignment_overrun_is_rejected_as_truncated_audio() -> None:
    speech = SegmentSpeech(
        pcm_s16le=b"\x00\x00" * 24_000,
        sample_rate=24_000,
        words=(RelativeWord("word", 900, 1_101),),
    )

    normalized = normalize_segment_speech(speech)

    assert normalized is speech
    with pytest.raises(AlignmentError, match="extends beyond"):
        validate_segment_speech(normalized)
