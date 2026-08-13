"""Detect FFmpeg/ffprobe features before expensive media work."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MediaCapabilities:
    ffmpeg_found: bool
    ffprobe_found: bool
    subtitles_filter: bool
    ass_filter: bool
    fontconfig: bool
    ffmpeg_version: str | None
    problems: tuple[str, ...]

    @property
    def ready_for_rendering(self) -> bool:
        return not self.problems


Runner = Callable[..., subprocess.CompletedProcess[str]]


def check_media_capabilities(
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: Runner = subprocess.run,
) -> MediaCapabilities:
    ffmpeg_executable = which(ffmpeg)
    ffprobe_executable = which(ffprobe)
    problems: list[str] = []
    if ffmpeg_executable is None:
        problems.append(f"FFmpeg executable not found: {ffmpeg}")
    if ffprobe_executable is None:
        problems.append(f"ffprobe executable not found: {ffprobe}")

    filters_output = ""
    version_output = ""
    version: str | None = None
    if ffmpeg_executable is not None:
        version_result = _run(runner, [ffmpeg_executable, "-version"])
        if version_result.returncode == 0:
            version_output = version_result.stdout
            version = version_output.splitlines()[0] if version_output else "unknown"
        else:
            problems.append("FFmpeg could not report its version.")
        filters_result = _run(runner, [ffmpeg_executable, "-hide_banner", "-filters"])
        if filters_result.returncode == 0:
            filters_output = filters_result.stdout
        else:
            problems.append("FFmpeg could not report available filters.")

    subtitles = _has_filter(filters_output, "subtitles")
    ass = _has_filter(filters_output, "ass")
    fontconfig = "--enable-libfontconfig" in version_output
    if ffmpeg_executable is not None and not subtitles:
        problems.append("FFmpeg is missing the subtitles/libass filter.")
    if ffmpeg_executable is not None and not ass:
        problems.append("FFmpeg is missing the ass/libass filter.")
    if ffmpeg_executable is not None and not fontconfig:
        problems.append("FFmpeg does not report fontconfig support.")

    return MediaCapabilities(
        ffmpeg_found=ffmpeg_executable is not None,
        ffprobe_found=ffprobe_executable is not None,
        subtitles_filter=subtitles,
        ass_filter=ass,
        fontconfig=fontconfig,
        ffmpeg_version=version,
        problems=tuple(problems),
    )


def _run(runner: Runner, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            arguments,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(arguments, 1, "", str(exc))


def _has_filter(output: str, name: str) -> bool:
    return any(
        len(parts := line.split()) >= 2 and parts[1] == name for line in output.splitlines()
    )
