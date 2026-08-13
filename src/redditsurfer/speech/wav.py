"""Standard-library WAV encoding and decoding for mono signed 16-bit PCM."""

from __future__ import annotations

import io
import wave

from redditsurfer.errors import SpeechError


def encode_wav(pcm_s16le: bytes, sample_rate: int) -> bytes:
    if sample_rate <= 0 or len(pcm_s16le) % 2:
        raise SpeechError("Cannot encode invalid signed 16-bit PCM as WAV.")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm_s16le)
    return buffer.getvalue()


def decode_wav(value: bytes) -> tuple[bytes, int]:
    try:
        with wave.open(io.BytesIO(value), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise SpeechError("Cached speech WAV must be mono signed 16-bit PCM.")
            if source.getcomptype() != "NONE":
                raise SpeechError("Cached speech WAV must be uncompressed PCM.")
            sample_rate = source.getframerate()
            frames = source.readframes(source.getnframes())
    except (EOFError, wave.Error) as exc:
        raise SpeechError("Cached speech WAV is corrupt.") from exc
    return frames, sample_rate
