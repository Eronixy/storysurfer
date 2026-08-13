from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from redditsurfer.config import SpeechConfig
from redditsurfer.domain import NarrationScript, NarrationSegment, SourceRef
from redditsurfer.errors import SpeechError
from redditsurfer.speech.base import RelativeWord, SegmentSpeech
from redditsurfer.speech.service import synthesize_script
from redditsurfer.speech.wav import decode_wav
from redditsurfer.storage import RunStorage, json_hash


@dataclass
class FakeSpeechProvider:
    calls: int = 0

    @property
    def provider_id(self) -> str:
        return "fake"

    def settings(self) -> dict[str, object]:
        return {"provider": "fake", "voice": "fixture", "sample_rate": 24_000}

    def synthesize(self, text: str) -> SegmentSpeech:
        self.calls += 1
        tokens = text.split()
        duration_ms = len(tokens) * 100
        pcm = b"\x00\x00" * round(duration_ms * 24_000 / 1_000)
        words = tuple(
            RelativeWord(word, index * 100, (index + 1) * 100)
            for index, word in enumerate(tokens)
        )
        return SegmentSpeech(pcm_s16le=pcm, sample_rate=24_000, words=words)


def _script() -> NarrationScript:
    source = SourceRef(
        source_type="post",
        source_id="abc123",
        permalink="https://www.reddit.com/comments/abc123/",
        author_role="op",
        original_text_hash="hash",
    )
    segments = (
        NarrationSegment(
            id="first",
            kind="title",
            speaker_label="Narrator",
            spoken_text="First segment",
            original_excerpt="First segment",
            source_refs=(source,),
            priority=1.0,
        ),
        NarrationSegment(
            id="second",
            kind="post",
            speaker_label="OP",
            spoken_text="Second spoken segment",
            original_excerpt="Second spoken segment",
            source_refs=(source,),
            priority=1.0,
        ),
    )
    return NarrationScript(
        thread_id="abc123",
        created_at="2026-01-01T00:00:00+00:00",
        target_duration_seconds=30,
        estimated_duration_ms=600,
        estimated_words=5,
        segments=segments,
    )


def test_speech_is_composed_with_pause_and_monotonic_words(tmp_path: Path) -> None:
    storage = RunStorage(tmp_path / "runs")
    run_id = storage.create_run({}, run_id="speech-run")
    provider = FakeSpeechProvider()
    config = SpeechConfig(sample_rate=24_000, segment_pause_ms=100)

    artifact = synthesize_script(run_id, _script(), provider, config, storage)

    assert provider.calls == 2
    assert artifact.duration_ms == 600
    assert [word.start_ms for word in artifact.words] == [0, 100, 300, 400, 500]
    assert all(
        current.end_ms <= following.start_ms
        for current, following in zip(artifact.words, artifact.words[1:], strict=False)
    )
    pcm, sample_rate = decode_wav(storage.internal_path(run_id, artifact.audio_path).read_bytes())
    assert sample_rate == 24_000
    assert round(len(pcm) / 2 / sample_rate * 1_000) == artifact.duration_ms


def test_unchanged_segments_reuse_cache_without_provider_calls(tmp_path: Path) -> None:
    storage = RunStorage(tmp_path / "runs")
    run_id = storage.create_run({}, run_id="cache-run")
    config = SpeechConfig(sample_rate=24_000, segment_pause_ms=100)
    first_provider = FakeSpeechProvider()
    synthesize_script(run_id, _script(), first_provider, config, storage)
    second_provider = FakeSpeechProvider()

    second = synthesize_script(run_id, _script(), second_provider, config, storage)

    assert first_provider.calls == 2
    assert second_provider.calls == 0
    assert second.duration_ms == 600


def test_incomplete_cache_does_not_trigger_an_uncertain_paid_retry(tmp_path: Path) -> None:
    storage = RunStorage(tmp_path / "runs")
    run_id = storage.create_run({}, run_id="partial-cache-run")
    config = SpeechConfig(sample_rate=24_000, segment_pause_ms=100)
    provider = FakeSpeechProvider()
    first = _script().segments[0]

    cache_key = json_hash(
        {"spoken_text": first.spoken_text, "provider_settings": provider.settings()}
    )
    storage.write_bytes(run_id, f"speech/cache/{cache_key}.wav", b"partial")

    with pytest.raises(SpeechError, match="incomplete"):
        synthesize_script(run_id, _script(), provider, config, storage)

    assert provider.calls == 0
