"""Edge TTS adapter normalized from streamed MP3 to PCM and word timing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any, cast

import aiohttp
import edge_tts
import miniaudio
from edge_tts.exceptions import EdgeTTSException

from redditsurfer.config import SpeechConfig
from redditsurfer.domain import JsonValue
from redditsurfer.errors import ConfigurationError, SpeechError
from redditsurfer.speech.base import RelativeWord, SegmentSpeech

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
                    words.append(_word_boundary(event))
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
        return SegmentSpeech(
            pcm_s16le=pcm,
            sample_rate=self._config.sample_rate,
            words=tuple(words),
        )


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


def _word_boundary(event: dict[str, object]) -> RelativeWord:
    text = event.get("text")
    offset = event.get("offset")
    duration = event.get("duration")
    if (
        not isinstance(text, str)
        or not text.strip()
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or not isinstance(duration, int)
        or isinstance(duration, bool)
        or offset < 0
        or duration <= 0
    ):
        raise SpeechError("Edge TTS returned an invalid WordBoundary event.")
    return RelativeWord(
        text=text,
        start_ms=round(offset / TICKS_PER_MILLISECOND),
        end_ms=round((offset + duration) / TICKS_PER_MILLISECOND),
    )
