"""Pure media timeline construction."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from redditsurfer.config import MediaConfig
from redditsurfer.domain import CaptionArtifact, SpeechArtifact, Timeline
from redditsurfer.errors import MediaError
from redditsurfer.media.probe import MediaInfo


def build_timeline(
    speech: SpeechArtifact,
    captions: CaptionArtifact,
    background: MediaInfo,
    media: MediaConfig,
    *,
    preset: Literal["subway", "minecraft"],
    crop_offset: float = 0.0,
) -> Timeline:
    if not -1.0 <= crop_offset <= 1.0:
        raise MediaError("Background crop offset must be between -1 and 1.")
    if captions.duration_ms != speech.duration_ms:
        raise MediaError("Caption and narration durations do not match.")
    if captions.cues[-1].end_ms > speech.duration_ms:
        raise MediaError("Caption timing extends beyond narration duration.")
    if media.output_width % 2 or media.output_height % 2:
        raise MediaError("H.264 output dimensions must both be even numbers.")
    narration_path = Path(speech.audio_path)
    captions_path = Path(captions.ass_path)
    if narration_path.is_absolute() or captions_path.is_absolute():
        raise MediaError("Run narration and caption paths must be relative.")
    return Timeline(
        duration_ms=speech.duration_ms,
        background_path=str(background.path),
        background_duration_ms=background.duration_ms,
        background_has_audio=background.has_audio,
        background_looped=background.duration_ms < speech.duration_ms,
        preset=preset,
        crop_offset=crop_offset,
        narration_path=speech.audio_path,
        captions_path=captions.ass_path,
        output_width=media.output_width,
        output_height=media.output_height,
        frame_rate=media.frame_rate,
        retain_background_audio=media.retain_background_audio and background.has_audio,
    )
