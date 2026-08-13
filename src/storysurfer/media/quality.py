"""Objective ffprobe-based checks for rendered delivery artifacts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from storysurfer.config import MediaConfig
from storysurfer.domain import (
    CaptionArtifact,
    QualityCheck,
    Timeline,
    VerificationReport,
)
from storysurfer.media.probe import probe_media


def verify_rendered_media(
    output: Path,
    timeline: Timeline,
    captions: CaptionArtifact,
    media: MediaConfig,
    *,
    profile: Literal["preview", "final"],
    artifact_integrity: bool = True,
    now: Callable[[], str] | None = None,
) -> VerificationReport:
    info = probe_media(output, media.ffprobe_path)
    checks = (
        _check(
            "dimensions",
            (info.width, info.height) == (timeline.output_width, timeline.output_height),
            f"expected {timeline.output_width}x{timeline.output_height}; "
            f"found {info.width}x{info.height}",
        ),
        _check(
            "frame_rate",
            abs(info.frame_rate - timeline.frame_rate) <= 0.01,
            f"expected {timeline.frame_rate} fps; found {info.frame_rate:.3f} fps",
        ),
        _check(
            "duration",
            abs(info.duration_ms - timeline.duration_ms) <= media.duration_tolerance_ms,
            f"expected {timeline.duration_ms} ms ± {media.duration_tolerance_ms} ms; "
            f"found {info.duration_ms} ms",
        ),
        _check("video_codec", info.video_codec == "h264", f"found {info.video_codec or 'none'}"),
        _check("audio_stream", info.has_audio, f"found {info.audio_codec or 'no audio'}"),
        _check("audio_codec", info.audio_codec == "aac", f"found {info.audio_codec or 'none'}"),
        _check(
            "pixel_format",
            info.pixel_format == "yuv420p",
            f"found {info.pixel_format or 'unknown'}",
        ),
        _check(
            "caption_bounds",
            all(0 <= cue.start_ms < cue.end_ms <= timeline.duration_ms for cue in captions.cues),
            "all cue times must stay within the rendered narration timeline",
        ),
        _check(
            "rights_acknowledgement",
            profile == "preview" or timeline.rights_acknowledged,
            "required for final renders",
        ),
        _check(
            "artifact_integrity",
            artifact_integrity,
            "all required run artifacts must exist and match their manifest checksums",
        ),
    )
    return VerificationReport(
        profile=profile,
        artifact_path=output.name,
        checked_at=(now or _utc_now)(),
        passed=all(check.passed for check in checks),
        duration_ms=info.duration_ms,
        width=info.width,
        height=info.height,
        frame_rate=info.frame_rate,
        video_codec=info.video_codec,
        audio_codec=info.audio_codec,
        pixel_format=info.pixel_format,
        checks=checks,
    )


def _check(name: str, passed: bool, detail: str) -> QualityCheck:
    return QualityCheck(name=name, passed=passed, message=detail)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
