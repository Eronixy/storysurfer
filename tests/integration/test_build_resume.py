from __future__ import annotations

import math
import shutil
import struct
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from redditsurfer.config import AppConfig, CaptionConfig, MediaConfig
from redditsurfer.domain import JsonValue, ThreadSnapshot, Timeline
from redditsurfer.errors import MediaError, RightsError
from redditsurfer.pipeline import build
from redditsurfer.speech.base import RelativeWord, SegmentSpeech
from redditsurfer.storage import RunStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="end-to-end build test requires ffmpeg and ffprobe",
)


@dataclass
class CountingSpeech:
    calls: int = 0

    @property
    def provider_id(self) -> str:
        return "counting-fake"

    def settings(self) -> dict[str, JsonValue]:
        return {"provider": self.provider_id, "sample_rate": 24_000}

    def synthesize(self, text: str) -> SegmentSpeech:
        self.calls += 1
        tokens = text.split()
        word_ms = 50
        duration_ms = max(word_ms, len(tokens) * word_ms)
        frame_count = round(duration_ms * 24_000 / 1_000)
        pcm = b"".join(
            struct.pack(
                "<h", round(3_000 * math.sin(2 * math.pi * 440 * frame / 24_000))
            )
            for frame in range(frame_count)
        )
        words = tuple(
            RelativeWord(token, index * word_ms, (index + 1) * word_ms)
            for index, token in enumerate(tokens)
        )
        return SegmentSpeech(pcm_s16le=pcm, sample_rate=24_000, words=words)


def test_failed_render_resumes_without_repeating_speech(
    app_config: AppConfig,
    thread_snapshot: ThreadSnapshot,
    tmp_path: Path,
) -> None:
    media = MediaConfig(
        output_width=180,
        output_height=320,
        crf=30,
        encoder_preset="ultrafast",
        preview_width=180,
        preview_height=320,
        preview_crf=30,
        preview_encoder_preset="ultrafast",
    )
    captions = CaptionConfig(
        font_size=20,
        outline_size=2,
        margin_horizontal=10,
        margin_bottom=60,
    )
    config = replace(
        app_config,
        selection=replace(app_config.selection, target_duration_seconds=15),
        media=media,
        captions=captions,
    )
    storage = RunStorage(config.storage.runs_dir)
    speech = CountingSpeech()
    background = tmp_path / "licensed-synthetic-gameplay.mp4"
    _make_background(background)
    failed_render_calls = 0

    def fail_renderer(
        run_dir: Path,
        timeline: Timeline,
        render_media: MediaConfig,
        output_name: str,
    ) -> Path:
        nonlocal failed_render_calls
        failed_render_calls += 1
        raise MediaError("synthetic interrupted render")

    with pytest.raises(MediaError, match="interrupted") as failure:
        build(
            background,
            "minecraft",
            config,
            storage,
            cached_thread=thread_snapshot,
            speech_factory=lambda _: speech,
            renderer=fail_renderer,
        )

    run_directories = [path for path in config.storage.runs_dir.iterdir() if path.is_dir()]
    assert len(run_directories) == 1
    run_id = run_directories[0].name
    calls_after_failure = speech.calls
    assert calls_after_failure > 0
    assert failed_render_calls == 1
    assert run_id in (failure.value.hint or "")
    assert storage.stage_statuses(run_id)["render_preview"] == "failed"

    completed = build(
        background,
        "minecraft",
        config,
        storage,
        resume_run_id=run_id,
        speech_factory=lambda _: speech,
    )

    assert completed.verification.passed
    assert speech.calls == calls_after_failure
    output = storage.artifact_path(run_id, "preview.mp4")
    output_mtime = output.stat().st_mtime_ns

    repeated = build(
        background,
        "minecraft",
        config,
        storage,
        resume_run_id=run_id,
        speech_factory=lambda _: speech,
    )

    assert repeated.verification == completed.verification
    assert speech.calls == calls_after_failure
    assert output.stat().st_mtime_ns == output_mtime
    assert storage.stage_statuses(run_id)["verify_preview"] == "completed"

    final = build(
        background,
        "minecraft",
        config,
        storage,
        resume_run_id=run_id,
        profile="final",
        acknowledge_rights=True,
        speech_factory=lambda _: speech,
    )

    assert final.verification.passed
    assert speech.calls == calls_after_failure
    assert storage.artifact_path(run_id, "final.mp4").is_file()
    assert storage.artifact_path(run_id, "verification.json").is_file()
    before_style_change = storage.read_manifest(run_id)

    restyled_config = replace(
        config,
        captions=replace(config.captions, highlight_color="#00FF00"),
    )
    restyled = build(
        background,
        "minecraft",
        restyled_config,
        storage,
        resume_run_id=run_id,
        speech_factory=lambda _: speech,
    )

    after_style_change = storage.read_manifest(run_id)
    assert restyled.verification.passed
    assert speech.calls == calls_after_failure
    assert (
        before_style_change["stages"]["caption"]["input_hash"]
        != after_style_change["stages"]["caption"]["input_hash"]
    )
    assert after_style_change["stages"]["render_final"]["status"] == "stale"
    assert storage.artifacts_are_current(
        run_id,
        (
            "thread",
            "selection",
            "script",
            "script_report",
            "speech",
            "narration_audio",
            "captions",
            "captions_ass",
            "captions_srt",
            "timeline_preview",
            "preview",
            "verification_preview",
            "timeline_final",
            "final",
            "verification_final",
        ),
    )


def test_final_build_requires_rights_before_creating_a_run(
    app_config: AppConfig,
    thread_snapshot: ThreadSnapshot,
    tmp_path: Path,
) -> None:
    storage = RunStorage(tmp_path / "runs")

    with pytest.raises(RightsError, match="acknowledge-rights"):
        build(
            tmp_path / "unused.mp4",
            "subway",
            app_config,
            storage,
            cached_thread=thread_snapshot,
            profile="final",
            acknowledge_rights=False,
        )

    assert not storage.root.exists()


def _make_background(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
        timeout=30,
    )
