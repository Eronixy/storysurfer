"""Per-segment speech caching and monotonic narration composition."""

from __future__ import annotations

from typing import cast

from redditsurfer.config import SpeechConfig
from redditsurfer.domain import JsonValue, NarrationScript, SpeechArtifact, SpokenWord
from redditsurfer.errors import AlignmentError, SpeechError, StorageError
from redditsurfer.speech.alignment import validate_segment_speech
from redditsurfer.speech.base import RelativeWord, SegmentSpeech, SpeechProvider
from redditsurfer.speech.wav import decode_wav, encode_wav
from redditsurfer.storage import RunStorage, json_hash


def synthesize_script(
    run_id: str,
    script: NarrationScript,
    provider: SpeechProvider,
    config: SpeechConfig,
    storage: RunStorage,
) -> SpeechArtifact:
    settings = provider.settings()
    segment_audio: list[tuple[str, SegmentSpeech]] = []
    cache_keys: dict[str, str] = {}
    for segment in script.segments:
        cache_key = json_hash(
            {"spoken_text": segment.spoken_text, "provider_settings": settings}
        )
        speech = _load_or_synthesize(
            run_id,
            cache_key,
            segment.spoken_text,
            provider,
            storage,
        )
        validate_segment_speech(speech)
        if speech.sample_rate != config.sample_rate:
            raise SpeechError(
                f"Speech provider returned {speech.sample_rate} Hz; "
                f"expected {config.sample_rate} Hz."
            )
        segment_audio.append((segment.id, speech))
        cache_keys[segment.id] = cache_key

    combined_pcm = bytearray()
    absolute_words: list[SpokenWord] = []
    pause_frames = round(config.segment_pause_ms * config.sample_rate / 1_000)
    silence = b"\x00\x00" * pause_frames
    for index, (segment_id, speech) in enumerate(segment_audio):
        offset_ms = round(len(combined_pcm) / 2 / config.sample_rate * 1_000)
        for word in speech.words:
            absolute_words.append(
                SpokenWord(
                    text=word.text,
                    start_ms=offset_ms + word.start_ms,
                    end_ms=offset_ms + word.end_ms,
                    segment_id=segment_id,
                )
            )
        combined_pcm.extend(speech.pcm_s16le)
        if index < len(segment_audio) - 1:
            combined_pcm.extend(silence)

    duration_ms = round(len(combined_pcm) / 2 / config.sample_rate * 1_000)
    _validate_absolute_words(absolute_words, duration_ms)
    audio_path = "speech/narration.wav"
    storage.write_bytes(run_id, audio_path, encode_wav(bytes(combined_pcm), config.sample_rate))
    artifact = SpeechArtifact(
        provider_id=provider.provider_id,
        audio_path=audio_path,
        duration_ms=duration_ms,
        sample_rate=config.sample_rate,
        words=tuple(absolute_words),
        segment_cache_keys=cache_keys,
    )
    storage.write_speech(run_id, artifact)
    return artifact


def _load_or_synthesize(
    run_id: str,
    cache_key: str,
    text: str,
    provider: SpeechProvider,
    storage: RunStorage,
) -> SegmentSpeech:
    wav_path = f"speech/cache/{cache_key}.wav"
    metadata_path = f"speech/cache/{cache_key}.json"
    wav_exists = storage.internal_path(run_id, wav_path).is_file()
    metadata_exists = storage.internal_path(run_id, metadata_path).is_file()
    if wav_exists != metadata_exists:
        raise SpeechError(
            f"Speech cache entry is incomplete: {cache_key[:12]}",
            hint="Preserve the run for review and remove only this cache entry before retrying.",
        )
    if wav_exists:
        return _read_cache(run_id, cache_key, wav_path, metadata_path, storage)

    speech = provider.synthesize(text)
    validate_segment_speech(speech)
    storage.write_bytes(run_id, wav_path, encode_wav(speech.pcm_s16le, speech.sample_rate))
    metadata: dict[str, JsonValue] = {
        "cache_key": cache_key,
        "provider_id": provider.provider_id,
        "sample_rate": speech.sample_rate,
        "words": [
            {"text": word.text, "start_ms": word.start_ms, "end_ms": word.end_ms}
            for word in speech.words
        ],
    }
    storage.write_json_internal(run_id, metadata_path, metadata)
    return speech


def _read_cache(
    run_id: str,
    cache_key: str,
    wav_path: str,
    metadata_path: str,
    storage: RunStorage,
) -> SegmentSpeech:
    try:
        metadata = storage.read_json_internal(run_id, metadata_path)
        pcm, sample_rate = decode_wav(storage.internal_path(run_id, wav_path).read_bytes())
    except (OSError, StorageError) as exc:
        raise SpeechError(f"Could not read speech cache entry: {cache_key[:12]}") from exc
    if not isinstance(metadata, dict):
        raise SpeechError(f"Speech cache metadata is invalid: {cache_key[:12]}")
    data = cast(dict[object, object], metadata)
    if data.get("cache_key") != cache_key or data.get("sample_rate") != sample_rate:
        raise SpeechError(f"Speech cache metadata does not match audio: {cache_key[:12]}")
    raw_words = data.get("words")
    if not isinstance(raw_words, list):
        raise SpeechError(f"Speech cache has no word timing data: {cache_key[:12]}")
    words: list[RelativeWord] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, dict):
            raise SpeechError(f"Speech cache word timing is invalid: {cache_key[:12]}")
        word_data = cast(dict[object, object], raw_word)
        text = word_data.get("text")
        start = word_data.get("start_ms")
        end = word_data.get("end_ms")
        if (
            not isinstance(text, str)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
        ):
            raise SpeechError(f"Speech cache word timing is invalid: {cache_key[:12]}")
        words.append(RelativeWord(text=text, start_ms=start, end_ms=end))
    speech = SegmentSpeech(pcm_s16le=pcm, sample_rate=sample_rate, words=tuple(words))
    validate_segment_speech(speech)
    return speech


def _validate_absolute_words(words: list[SpokenWord], duration_ms: int) -> None:
    if not words:
        raise AlignmentError("Composed narration has no word timings.")
    previous_end = 0
    for word in words:
        if word.start_ms < previous_end or word.end_ms <= word.start_ms:
            raise AlignmentError("Composed narration word timings are not monotonic.")
        previous_end = word.end_ms
    if words[-1].end_ms > duration_ms:
        raise AlignmentError("Composed narration alignment extends beyond its audio duration.")
