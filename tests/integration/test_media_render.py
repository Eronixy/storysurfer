from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from redditsurfer.captions.ass import render_ass
from redditsurfer.captions.srt import render_srt
from redditsurfer.config import AppConfig, CaptionConfig, MediaConfig
from redditsurfer.domain import CaptionArtifact, CaptionCue, CaptionWord, SpeechArtifact
from redditsurfer.pipeline import preview
from redditsurfer.speech.wav import encode_wav
from redditsurfer.storage import RunStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg integration test requires ffmpeg and ffprobe",
)


def test_short_vertical_preview_has_expected_streams_and_timing(
    app_config: AppConfig, tmp_path: Path
) -> None:
    media = MediaConfig(
        output_width=180,
        output_height=320,
        frame_rate=30,
        crf=28,
        encoder_preset="ultrafast",
        preview_width=180,
        preview_height=320,
        preview_crf=28,
        preview_encoder_preset="ultrafast",
    )
    caption_config = CaptionConfig(
        font_size=20,
        outline_size=2,
        margin_horizontal=12,
        margin_bottom=64,
    )
    config = replace(app_config, media=media, captions=caption_config)
    storage = RunStorage(config.storage.runs_dir)
    run_id = storage.create_run(config.public_dict(), run_id="media-render-run")
    duration_ms = 800
    frame_count = round(duration_ms * 24_000 / 1_000)
    pcm = b"".join(
        struct.pack("<h", round(4_000 * math.sin(2 * math.pi * 440 * frame / 24_000)))
        for frame in range(frame_count)
    )
    speech = SpeechArtifact(
        provider_id="fake",
        audio_path="speech/narration.wav",
        duration_ms=duration_ms,
        sample_rate=24_000,
        words=(),
        segment_cache_keys={},
    )
    storage.write_bytes(run_id, speech.audio_path, encode_wav(pcm, 24_000))
    storage.write_speech(run_id, speech)
    cue = CaptionCue(
        id="cue-synthetic",
        text="Synthetic\ntimed caption",
        start_ms=50,
        end_ms=750,
        segment_id="story",
        speaker_label="OP",
        style="story",
        words=(
            CaptionWord("Synthetic", 50, 250),
            CaptionWord("timed", 300, 500),
            CaptionWord("caption", 550, 750),
        ),
    )
    captions = CaptionArtifact(duration_ms, "captions.ass", "captions.srt", (cue,))
    storage.write_text(run_id, captions.ass_path, render_ass(captions, caption_config, media))
    storage.write_text(run_id, captions.srt_path, render_srt(captions))
    storage.write_captions(run_id, captions)
    background = tmp_path / "licensed synthetic gameplay.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=0.4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(background),
        ],
        check=True,
        timeout=30,
    )

    timeline = preview(run_id, background, "minecraft", config, storage)

    output = storage.artifact_path(run_id, "preview.mp4")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    metadata = cast(dict[str, object], json.loads(result.stdout))
    streams = cast(list[dict[str, object]], metadata["streams"])
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    file_format = cast(dict[str, object], metadata["format"])

    assert timeline.background_looped
    assert (video["width"], video["height"]) == (180, 320)
    assert video["avg_frame_rate"] == "30/1"
    assert audio["codec_name"] == "aac"
    assert float(cast(str, file_format["duration"])) == pytest.approx(0.8, abs=0.08)
    assert output.stat().st_size > 0
