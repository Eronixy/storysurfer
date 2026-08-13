"""Safe FFmpeg command construction and atomic preview rendering."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from redditsurfer.config import MediaConfig
from redditsurfer.domain import Timeline
from redditsurfer.errors import MediaError

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
    output = (run_dir / output_name).resolve()
    resolved_run_dir = run_dir.resolve()
    if output.parent != resolved_run_dir or output.suffix.lower() != ".mp4":
        raise MediaError("Preview output must be an MP4 directly inside the run directory.")
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
            dir=run_dir, prefix=".preview.", suffix=".mp4", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        temporary_path.unlink()
        command = build_ffmpeg_command(timeline, media, temporary_path)
        result = runner(
            command,
            cwd=run_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(60, round(timeline.duration_ms / 1_000) * 10),
        )
        if result.returncode != 0:
            detail = (
                result.stderr.strip().splitlines()[-1]
                if result.stderr.strip()
                else "unknown error"
            )
            raise MediaError(
                f"FFmpeg preview render failed: {detail}",
                hint="Run redditsurfer doctor and verify the gameplay file and configured codec.",
            )
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise MediaError("FFmpeg reported success but produced no preview video.")
        temporary_path.replace(output)
        return output
    except FileNotFoundError as exc:
        raise MediaError(
            f"FFmpeg executable not found: {media.ffmpeg_path}",
            hint="Install FFmpeg or configure media.ffmpeg_path.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("FFmpeg preview render timed out.") from exc
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
