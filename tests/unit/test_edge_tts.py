from __future__ import annotations

from redditsurfer.config import SpeechConfig
from redditsurfer.speech.alignment import validate_segment_speech
from redditsurfer.speech.edge import EdgeTTSSpeechProvider


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
