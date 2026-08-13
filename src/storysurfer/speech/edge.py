"""Edge TTS adapter normalized from streamed MP3 to PCM and word timing."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterable
from typing import Any, cast

import aiohttp
import edge_tts
import miniaudio
from edge_tts.exceptions import EdgeTTSException

from storysurfer.config import SpeechConfig
from storysurfer.domain import JsonValue
from storysurfer.errors import AlignmentError, ConfigurationError, SpeechError
from storysurfer.speech.alignment import normalize_segment_speech, validate_segment_speech
from storysurfer.speech.base import RelativeWord, SegmentSpeech

MAX_EDGE_CHUNK_CHARS = 1_000
MAX_EDGE_WORD_DURATION_MS = 10_000
FALLBACK_EDGE_WORD_DURATION_MS = 500
MAX_EDGE_CHUNK_ATTEMPTS = 3
TICKS_PER_MILLISECOND = 10_000
StreamFactory = Callable[[str, SpeechConfig], Iterable[object]]
Mp3Decoder = Callable[[bytes, int], bytes]


class EdgeTTSSpeechProvider:
    """Stream Edge speech and WordBoundary events, then decode MP3 to mono PCM."""

    def __init__(
        self,
        config: SpeechConfig,
        *,
        stream_factory: StreamFactory | None = None,
        mp3_decoder: Mp3Decoder | None = None,
    ) -> None:
        if config.provider != "edge-tts":
            raise ConfigurationError(
                f"Edge TTS adapter received incompatible provider: {config.provider}"
            )
        if not config.voice:
            raise ConfigurationError(
                "Edge TTS voice is not configured.",
                hint="Set speech.voice or run `uv run edge-tts --list-voices`.",
            )
        if config.sample_rate != 24_000:
            raise ConfigurationError(
                "The Edge TTS adapter requires 24 kHz output.",
                hint="Set speech sample rate to 24000 or use a compatible provider adapter.",
            )
        self._config = config
        self._stream_factory = stream_factory or _edge_stream
        self._mp3_decoder = mp3_decoder or _decode_mp3

    @property
    def provider_id(self) -> str:
        return "edge-tts"

    def settings(self) -> dict[str, JsonValue]:
        return {
            "provider": self.provider_id,
            "voice": self._config.voice,
            "rate": self._config.rate,
            "volume": self._config.volume,
            "pitch": self._config.pitch,
            "output_format": "audio-24khz-48kbitrate-mono-mp3",
            "sample_rate": self._config.sample_rate,
        }

    def synthesize(self, text: str) -> SegmentSpeech:
        if not text.strip():
            raise SpeechError("Cannot synthesize an empty narration segment.")
        pcm = bytearray()
        words: list[RelativeWord] = []
        for chunk in _text_chunks(text):
            chunk_speech = self._synthesize_chunk(chunk)
            offset_ms = round(len(pcm) / 2 / self._config.sample_rate * 1_000)
            words.extend(
                RelativeWord(
                    text=word.text,
                    start_ms=offset_ms + word.start_ms,
                    end_ms=offset_ms + word.end_ms,
                )
                for word in chunk_speech.words
            )
            pcm.extend(chunk_speech.pcm_s16le)
        return SegmentSpeech(
            pcm_s16le=bytes(pcm),
            sample_rate=self._config.sample_rate,
            words=tuple(words),
        )

    def _synthesize_chunk(self, text: str) -> SegmentSpeech:
        alignment_error: AlignmentError | None = None
        for _attempt in range(MAX_EDGE_CHUNK_ATTEMPTS):
            try:
                return self._stream_chunk(text)
            except AlignmentError as exc:
                alignment_error = exc
        assert alignment_error is not None
        raise alignment_error

    def _stream_chunk(self, text: str) -> SegmentSpeech:
        mp3 = bytearray()
        words: list[RelativeWord] = []
        try:
            for raw_event in self._stream_factory(text, self._config):
                event = _event_mapping(raw_event)
                event_type = event.get("type")
                if event_type == "audio":
                    data = event.get("data")
                    if not isinstance(data, bytes) or not data:
                        raise SpeechError("Edge TTS returned an invalid audio chunk.")
                    mp3.extend(data)
                elif event_type == "WordBoundary":
                    boundary = _word_boundary(event)
                    if boundary is not None:
                        words.append(boundary)
        except (EdgeTTSException, aiohttp.ClientError, TimeoutError) as exc:
            raise SpeechError(
                "Edge TTS could not synthesize the narration segment.",
                hint=(
                    "Check network access and the configured voice, then retry; "
                    "cached segments remain."
                ),
            ) from exc
        if not mp3:
            raise SpeechError("Edge TTS returned no audio.")
        if not words:
            raise SpeechError("Edge TTS returned no WordBoundary timing events.")
        try:
            pcm = self._mp3_decoder(bytes(mp3), self._config.sample_rate)
        except miniaudio.DecodeError as exc:
            raise SpeechError("Edge TTS MP3 audio could not be decoded.") from exc
        speech = normalize_segment_speech(
            SegmentSpeech(
                pcm_s16le=pcm,
                sample_rate=self._config.sample_rate,
                words=tuple(_normalize_edge_words(words, len(pcm) // 2, self._config.sample_rate)),
            )
        )
        validate_segment_speech(speech)
        return speech


def _normalize_edge_words(
    words: list[RelativeWord], pcm_frames: int, sample_rate: int
) -> list[RelativeWord]:
    """Repair impossible per-word durations while preserving valid timing."""
    audio_duration_ms = round(pcm_frames / sample_rate * 1_000)
    normalized: list[RelativeWord] = []
    for index, word in enumerate(words):
        if word.end_ms - word.start_ms <= MAX_EDGE_WORD_DURATION_MS:
            normalized.append(word)
            continue
        next_start = words[index + 1].start_ms if index + 1 < len(words) else audio_duration_ms
        fallback_end = min(
            word.start_ms + FALLBACK_EDGE_WORD_DURATION_MS,
            next_start,
            audio_duration_ms,
        )
        if fallback_end <= word.start_ms:
            normalized.append(word)
            continue
        normalized.append(RelativeWord(text=word.text, start_ms=word.start_ms, end_ms=fallback_end))
    return normalized


def _text_chunks(text: str, maximum: int = MAX_EDGE_CHUNK_CHARS) -> tuple[str, ...]:
    """Split long narration at natural boundaries without dropping spoken text."""
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > maximum:
        window = remaining[: maximum + 1]
        sentence_ends = [
            index + 1
            for index, character in enumerate(window)
            if character in ".!?" and index + 1 < len(window) and window[index + 1].isspace()
        ]
        boundary = sentence_ends[-1] if sentence_ends else 0
        if boundary < maximum // 2:
            boundary = window.rfind(" ", 0, maximum + 1)
        if boundary <= 0:
            boundary = maximum
        chunks.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


def _edge_stream(text: str, config: SpeechConfig) -> Iterable[object]:
    communication = edge_tts.Communicate(
        text,
        config.voice,
        rate=config.rate,
        volume=config.volume,
        pitch=config.pitch,
        boundary="WordBoundary",
        connect_timeout=config.connect_timeout_seconds,
        receive_timeout=config.receive_timeout_seconds,
    )

    async def collect() -> list[object]:
        return [item async for item in communication.stream()]

    return asyncio.run(collect())


def _decode_mp3(value: bytes, sample_rate: int) -> bytes:
    decoded: Any = miniaudio.decode(
        value,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=sample_rate,
    )
    return cast(bytes, decoded.samples.tobytes())


def _event_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SpeechError("Edge TTS returned an invalid stream event.")
    return cast(dict[str, object], value)


def _word_boundary(event: dict[str, object]) -> RelativeWord | None:
    text = event.get("text")
    offset = event.get("offset")
    duration = event.get("duration")
    if (
        not isinstance(text, str)
        or not text.strip()
        or not isinstance(offset, int | float)
        or isinstance(offset, bool)
        or not isinstance(duration, int | float)
        or isinstance(duration, bool)
    ):
        raise SpeechError("Edge TTS returned an invalid WordBoundary event.")
    numeric_offset = float(offset)
    numeric_duration = float(duration)
    if (
        not math.isfinite(numeric_offset)
        or not math.isfinite(numeric_duration)
        or numeric_offset < 0
        or numeric_duration < 0
    ):
        raise SpeechError("Edge TTS returned an invalid WordBoundary event.")
    if numeric_duration == 0:
        return None
    start_ms = round(numeric_offset / TICKS_PER_MILLISECOND)
    end_ms = round((numeric_offset + numeric_duration) / TICKS_PER_MILLISECOND)
    return RelativeWord(
        text=text,
        start_ms=start_ms,
        end_ms=max(start_ms + 1, end_ms),
    )
