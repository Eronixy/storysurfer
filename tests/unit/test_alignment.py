from __future__ import annotations

import pytest

from storysurfer.errors import AlignmentError
from storysurfer.speech.alignment import words_from_character_alignment


def test_character_alignment_becomes_word_timing() -> None:
    characters = list("Hello world!")
    alignment = {
        "characters": characters,
        "character_start_times_seconds": [index * 0.05 for index in range(len(characters))],
        "character_end_times_seconds": [
            (index + 1) * 0.05 for index in range(len(characters))
        ],
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
