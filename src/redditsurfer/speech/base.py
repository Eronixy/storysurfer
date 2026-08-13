"""Provider-neutral speech synthesis contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from redditsurfer.domain import JsonValue


@dataclass(frozen=True, slots=True)
class RelativeWord:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class SegmentSpeech:
    pcm_s16le: bytes
    sample_rate: int
    words: tuple[RelativeWord, ...]

    @property
    def duration_ms(self) -> int:
        if self.sample_rate <= 0:
            return 0
        return round(len(self.pcm_s16le) / 2 / self.sample_rate * 1_000)


class SpeechProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def settings(self) -> dict[str, JsonValue]: ...

    def synthesize(self, text: str) -> SegmentSpeech: ...
