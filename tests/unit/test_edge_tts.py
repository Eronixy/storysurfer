from __future__ import annotations

from storysurfer.config import SpeechConfig
from storysurfer.speech.alignment import validate_segment_speech
from storysurfer.speech.edge import EdgeTTSSpeechProvider, _text_chunks


def test_edge_stream_is_normalized_to_pcm_words() -> None:
    captured: dict[str, object] = {}
    pcm = b"\x00\x00" * 24_000

    def stream(text: str, config: SpeechConfig):
        captured["text"] = text
        captured["voice"] = config.voice
        return iter(
            [
                {"type": "audio", "data": b"first-mp3-chunk"},
                {"type": "WordBoundary", "offset": 0, "duration": 2_500_000, "text": "Hello"},
                {"type": "audio", "data": b"second-mp3-chunk"},
                {
                    "type": "WordBoundary",
                    "offset": 3_000_000,
                    "duration": 2_500_000,
                    "text": "world",
                },
            ]
        )

    def decode(value: bytes, sample_rate: int) -> bytes:
        captured["mp3"] = value
        captured["sample_rate"] = sample_rate
        return pcm

    provider = EdgeTTSSpeechProvider(
        SpeechConfig(voice="en-US-FixtureNeural"),
        stream_factory=stream,
        mp3_decoder=decode,
    )
    speech = provider.synthesize("Hello world")

    assert captured["text"] == "Hello world"
    assert captured["voice"] == "en-US-FixtureNeural"
    assert captured["mp3"] == b"first-mp3-chunksecond-mp3-chunk"
    assert captured["sample_rate"] == 24_000
    assert [(word.text, word.start_ms, word.end_ms) for word in speech.words] == [
        ("Hello", 0, 250),
        ("world", 300, 550),
    ]
    validate_segment_speech(speech)


def test_provider_settings_include_every_voice_affecting_option() -> None:
    provider = EdgeTTSSpeechProvider(
        SpeechConfig(
            voice="en-GB-FixtureNeural",
            rate="+10%",
            volume="-5%",
            pitch="+2Hz",
        ),
        stream_factory=lambda _text, _config: (),
        mp3_decoder=lambda _value, _rate: b"",
    )

    assert provider.settings() == {
        "provider": "edge-tts",
        "voice": "en-GB-FixtureNeural",
        "rate": "+10%",
        "volume": "-5%",
        "pitch": "+2Hz",
        "output_format": "audio-24khz-48kbitrate-mono-mp3",
        "sample_rate": 24_000,
    }


def test_float_word_boundaries_are_accepted_and_rounded() -> None:
    pcm = b"\x00\x00" * 24_000
    provider = EdgeTTSSpeechProvider(
        SpeechConfig(voice="en-US-FixtureNeural"),
        stream_factory=lambda _text, _config: iter(
            [
                {"type": "audio", "data": b"mp3"},
                {
                    "type": "WordBoundary",
                    "offset": 1_250_000.0,
                    "duration": 1_000_000.5,
                    "text": "maganda",
                },
            ]
        ),
        mp3_decoder=lambda _value, _rate: pcm,
    )

    speech = provider.synthesize("maganda")

    assert [(word.text, word.start_ms, word.end_ms) for word in speech.words] == [
        ("maganda", 125, 225)
    ]
    validate_segment_speech(speech)


def test_zero_duration_nonspoken_boundary_is_ignored() -> None:
    pcm = b"\x00\x00" * 24_000
    provider = EdgeTTSSpeechProvider(
        SpeechConfig(voice="en-US-FixtureNeural"),
        stream_factory=lambda _text, _config: iter(
            [
                {"type": "audio", "data": b"mp3"},
                {
                    "type": "WordBoundary",
                    "offset": 0,
                    "duration": 0,
                    "text": "🤣",
                },
                {
                    "type": "WordBoundary",
                    "offset": 100_000,
                    "duration": 1_000_000,
                    "text": "maganda",
                },
            ]
        ),
        mp3_decoder=lambda _value, _rate: pcm,
    )

    speech = provider.synthesize("🤣 maganda")

    assert [word.text for word in speech.words] == ["maganda"]
    validate_segment_speech(speech)


def test_long_narration_is_chunked_with_monotonic_timing() -> None:
    calls: list[str] = []
    pcm = b"\x00\x00" * 24_000

    def stream(text: str, _config: SpeechConfig):
        calls.append(text)
        return iter(
            [
                {"type": "audio", "data": b"mp3"},
                {
                    "type": "WordBoundary",
                    "offset": 0,
                    "duration": 1_000_000,
                    "text": text.split()[0],
                },
            ]
        )

    provider = EdgeTTSSpeechProvider(
        SpeechConfig(voice="fil-PH-AngeloNeural"),
        stream_factory=stream,
        mp3_decoder=lambda _value, _rate: pcm,
    )
    text = " ".join(f"Sentence {index}." for index in range(120))

    speech = provider.synthesize(text)

    assert calls == list(_text_chunks(text))
    assert len(calls) > 1
    assert all(len(chunk) <= 1_000 for chunk in calls)
    assert " ".join(calls) == text
    assert [word.start_ms for word in speech.words] == [
        index * 1_000 for index in range(len(calls))
    ]
    validate_segment_speech(speech)


def test_transient_chunk_alignment_is_retried_in_place() -> None:
    calls = 0
    pcm = b"\x00\x00" * 24_000

    def stream(_text: str, _config: SpeechConfig):
        nonlocal calls
        calls += 1
        duration = 2_010_000 if calls < 3 else 1_000_000
        return iter(
            [
                {"type": "audio", "data": b"mp3"},
                {
                    "type": "WordBoundary",
                    "offset": 9_000_000,
                    "duration": duration,
                    "text": "salita",
                },
            ]
        )

    provider = EdgeTTSSpeechProvider(
        SpeechConfig(voice="fil-PH-AngeloNeural"),
        stream_factory=stream,
        mp3_decoder=lambda _value, _rate: pcm,
    )

    speech = provider.synthesize("salita")

    assert calls == 3
    assert speech.words[-1].end_ms == 1_000
    validate_segment_speech(speech)


def test_impossible_trailing_word_duration_is_repaired() -> None:
    calls = 0
    pcm = b"\x00\x00" * 24_000

    def stream(_text: str, _config: SpeechConfig):
        nonlocal calls
        calls += 1
        return iter(
            [
                {"type": "audio", "data": b"mp3"},
                {
                    "type": "WordBoundary",
                    "offset": 1_000_000,
                    "duration": 894_784_478_330,
                    "text": "Eme",
                },
            ]
        )

    provider = EdgeTTSSpeechProvider(
        SpeechConfig(voice="fil-PH-BlessicaNeural"),
        stream_factory=stream,
        mp3_decoder=lambda _value, _rate: pcm,
    )

    speech = provider.synthesize("Eme🤣")

    assert calls == 1
    assert speech.words[-1].start_ms == 100
    assert speech.words[-1].end_ms == 600
    validate_segment_speech(speech)
