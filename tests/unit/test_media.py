from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from redditsurfer.config import MediaConfig
from redditsurfer.domain import CaptionArtifact, CaptionCue, CaptionWord, SpeechArtifact
from redditsurfer.errors import MediaError
from redditsurfer.media.probe import MediaInfo, probe_media
from redditsurfer.media.render import build_ffmpeg_command
from redditsurfer.media.timeline import build_timeline


def _runner_with_metadata(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    metadata = {
        "streams": [
            {"codec_type": "video", "width": 640, "height": 360, "duration": "2.5"},
            {"codec_type": "audio"},
        ],
        "format": {"duration": "2.5"},
    }
    return subprocess.CompletedProcess([], 0, json.dumps(metadata), "")


def _artifacts() -> tuple[SpeechArtifact, CaptionArtifact]:
    speech = SpeechArtifact("fake", "speech/narration.wav", 3_000, 24_000, (), {})
    cue = CaptionCue(
        id="cue-1",
        text="Timed words",
        start_ms=0,
        end_ms=900,
        segment_id="story",
        speaker_label="OP",
        style="story",
        words=(CaptionWord("Timed", 0, 400), CaptionWord("words", 500, 900)),
    )
    captions = CaptionArtifact(3_000, "captions.ass", "captions.srt", (cue,))
    return speech, captions


def test_probe_requires_a_video_stream(tmp_path: Path) -> None:
    source = tmp_path / "gameplay.mp4"
    source.write_bytes(b"synthetic")

    info = probe_media(source, runner=_runner_with_metadata)

    assert info.width == 640
    assert info.height == 360
    assert info.duration_ms == 2_500
    assert info.has_audio


def test_timeline_loops_short_background_and_builds_safe_command(tmp_path: Path) -> None:
    speech, captions = _artifacts()
    background = MediaInfo(tmp_path / "background.mp4", 1_000, 640, 360, False)
    media = MediaConfig(output_width=360, output_height=640, frame_rate=30)

    timeline = build_timeline(
        speech, captions, background, media, preset="minecraft", crop_offset=0.25
    )
    command = build_ffmpeg_command(timeline, media, tmp_path / "preview.mp4")

    assert timeline.background_looped
    assert command[command.index("-filter_complex") + 1].count("crop=360:640") == 1
    assert command[command.index("-map") + 1] == "[vout]"
    assert str(background.path) in command


def test_timeline_rejects_out_of_range_crop_offset(tmp_path: Path) -> None:
    speech, captions = _artifacts()
    background = MediaInfo(tmp_path / "background.mp4", 1_000, 640, 360, False)

    with pytest.raises(MediaError, match="between -1 and 1"):
        build_timeline(
            speech, captions, background, MediaConfig(), preset="subway", crop_offset=1.1
        )
