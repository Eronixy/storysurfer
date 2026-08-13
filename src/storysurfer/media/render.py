"""Safe FFmpeg command construction and atomic preview rendering."""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

from storysurfer.config import MediaConfig
from storysurfer.domain import Timeline
from storysurfer.errors import MediaError

Runner = Callable[..., subprocess.CompletedProcess[str]]


def build_ffmpeg_command(
    timeline: Timeline,
    media: MediaConfig,
    output_path: Path,
) -> list[str]:
    duration = _seconds(timeline.duration_ms)
    scale_crop = _scale_crop_filter(timeline)
    video_filter = (
        f"[0:v]{scale_crop},fps={timeline.frame_rate},trim=duration={duration},"
        "setpts=PTS-STARTPTS,ass=filename=captions.ass[vout]"
    )
    voice = (
        f"[1:a]aresample=48000,atrim=duration={duration},asetpts=PTS-STARTPTS,"
        "loudnorm=I=-16:LRA=11:TP=-1.5"
    )
    if timeline.retain_background_audio:
        audio_filter = (
            f"[0:a]aresample=48000,atrim=duration={duration},asetpts=PTS-STARTPTS,"
            f"volume={media.background_volume}[background];"
            f"{voice}[voice];[background][voice]amix=inputs=2:duration=shortest:"
            "normalize=0,alimiter=limit=0.95[aout]"
        )
    else:
        audio_filter = f"{voice}[aout]"
    return [
        media.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-stream_loop",
        "-1",
        "-i",
        timeline.background_path,
        "-i",
        timeline.narration_path,
        "-filter_complex",
        f"{video_filter};{audio_filter}",
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-t",
        duration,
        "-r",
        str(timeline.frame_rate),
        "-c:v",
        media.video_codec,
        "-preset",
        media.encoder_preset,
        "-crf",
        str(media.crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        media.audio_codec,
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-y",
        str(output_path),
    ]


def render_preview(
    run_dir: Path,
    timeline: Timeline,
    media: MediaConfig,
    *,
    output_name: str = "preview.mp4",
    runner: Runner = subprocess.run,
) -> Path:
    return render_video(
        run_dir,
        timeline,
        media,
        output_name=output_name,
        runner=runner,
    )


def render_video(
    run_dir: Path,
    timeline: Timeline,
    media: MediaConfig,
    *,
    output_name: str,
    runner: Runner = subprocess.run,
    cancel_check: Callable[[], None] | None = None,
) -> Path:
    output = (run_dir / output_name).resolve()
    resolved_run_dir = run_dir.resolve()
    expected_name = "preview.mp4" if timeline.profile == "preview" else "final.mp4"
    if output.parent != resolved_run_dir or output.name != expected_name:
        raise MediaError(f"{timeline.profile.title()} output must be named {expected_name}.")
    narration = (run_dir / timeline.narration_path).resolve()
    captions = (run_dir / timeline.captions_path).resolve()
    if not narration.is_relative_to(resolved_run_dir) or not captions.is_relative_to(
        resolved_run_dir
    ):
        raise MediaError("Narration or caption path escapes the run directory.")
    if not narration.is_file():
        raise MediaError(f"Narration audio is missing: {timeline.narration_path}")
    if not captions.is_file():
        raise MediaError(f"ASS captions are missing: {timeline.captions_path}")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=run_dir, prefix=f".{timeline.profile}.", suffix=".mp4", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.unlink()
        command = build_ffmpeg_command(timeline, media, temporary_path)
        timeout = max(60, round(timeline.duration_ms / 1_000) * 10)
        result = (
            _run_cancellable(command, run_dir, timeout, cancel_check)
            if cancel_check is not None
            else runner(
                command,
                cwd=run_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        )
        if result.returncode != 0:
            detail = (
                result.stderr.strip().splitlines()[-1]
                if result.stderr.strip()
                else "unknown error"
            )
            raise MediaError(
                f"FFmpeg {timeline.profile} render failed: {detail}",
                hint="Run storysurfer doctor and verify the gameplay file and configured codec.",
            )
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise MediaError(
                f"FFmpeg reported success but produced no {timeline.profile} video."
            )
        temporary_path.replace(output)
        return output
    except FileNotFoundError as exc:
        raise MediaError(
            f"FFmpeg executable not found: {media.ffmpeg_path}",
            hint="Install FFmpeg or configure media.ffmpeg_path.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"FFmpeg {timeline.profile} render timed out.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _scale_crop_filter(timeline: Timeline) -> str:
    width = timeline.output_width
    height = timeline.output_height
    if timeline.preset == "subway":
        x = f"(in_w-out_w)/2+({timeline.crop_offset})*(in_w-out_w)/2"
        y = "(in_h-out_h)/2"
    else:
        x = "(in_w-out_w)/2"
        y = f"(in_h-out_h)/2+({timeline.crop_offset})*(in_h-out_h)/2"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:{x}:{y},setsar=1"
    )


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1_000:.3f}"


def _run_cancellable(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    cancel_check: Callable[[], None],
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        while True:
            cancel_check()
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                return subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        raise


def media_for_profile(
    media: MediaConfig, profile: Literal["preview", "final"]
) -> MediaConfig:
    if profile == "final":
        return media
    return replace(
        media,
        output_width=media.preview_width,
        output_height=media.preview_height,
        crf=media.preview_crf,
        encoder_preset=media.preview_encoder_preset,
    )
