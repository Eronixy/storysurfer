"""Typed ffprobe inspection and early gameplay validation."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from storysurfer.errors import MediaError


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: Path
    duration_ms: int
    width: int
    height: int
    has_audio: bool
    video_codec: str = ""
    audio_codec: str | None = None
    frame_rate: float = 0.0
    pixel_format: str | None = None


Runner = Callable[..., subprocess.CompletedProcess[str]]


def probe_media(
    path: Path,
    ffprobe: str = "ffprobe",
    *,
    runner: Runner = subprocess.run,
) -> MediaInfo:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise MediaError(f"Gameplay file does not exist: {path}")
    arguments = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(resolved),
    ]
    result = _run(runner, arguments)
    if result.returncode != 0:
        detail = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else "unknown error"
        )
        raise MediaError(
            f"ffprobe could not inspect gameplay: {detail}",
            hint="Use a local, non-DRM video file supported by FFmpeg.",
        )
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe returned invalid media metadata.") from exc
    if not isinstance(raw, dict):
        raise MediaError("ffprobe returned invalid media metadata.")
    data = cast(dict[str, object], raw)
    streams = data.get("streams")
    if not isinstance(streams, list):
        raise MediaError("ffprobe metadata has no stream list.")
    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(video, dict):
        raise MediaError("Gameplay input has no video stream.")
    width = _positive_int(video.get("width"), "video width")
    height = _positive_int(video.get("height"), "video height")
    duration = _duration_seconds(video.get("duration"))
    if duration is None:
        file_format = data.get("format")
        if isinstance(file_format, dict):
            duration = _duration_seconds(file_format.get("duration"))
    if duration is None or duration <= 0:
        raise MediaError("Gameplay input has no usable duration.")
    audio = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )
    video_codec = video.get("codec_name")
    pixel_format = video.get("pix_fmt")
    audio_codec = audio.get("codec_name") if isinstance(audio, dict) else None
    return MediaInfo(
        path=resolved,
        duration_ms=round(duration * 1_000),
        width=width,
        height=height,
        has_audio=audio is not None,
        video_codec=video_codec if isinstance(video_codec, str) else "",
        audio_codec=audio_codec if isinstance(audio_codec, str) else None,
        frame_rate=_frame_rate(video.get("avg_frame_rate")),
        pixel_format=pixel_format if isinstance(pixel_format, str) else None,
    )


def _run(runner: Runner, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            arguments,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise MediaError(
            f"ffprobe executable not found: {arguments[0]}",
            hint="Install FFmpeg or configure media.ffprobe_path.",
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError("ffprobe failed while inspecting gameplay.") from exc


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MediaError(f"Gameplay {label} is invalid.")
    return value


def _duration_seconds(value: object) -> float | None:
    try:
        result = float(value) if isinstance(value, str | int | float) else None
    except ValueError:
        return None
    return result if result is not None and result > 0 else None


def _frame_rate(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        numerator_text, denominator_text = value.split("/", 1)
        denominator = float(denominator_text)
        return float(numerator_text) / denominator if denominator else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0
