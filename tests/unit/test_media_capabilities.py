from __future__ import annotations

import subprocess
from collections.abc import Sequence

from redditsurfer.media.capabilities import check_media_capabilities


def test_reports_missing_binaries() -> None:
    report = check_media_capabilities(which=lambda _: None)

    assert not report.ready_for_rendering
    assert not report.ffmpeg_found
    assert not report.ffprobe_found


def test_detects_libass_and_fontconfig() -> None:
    def runner(
        arguments: Sequence[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        if "-version" in arguments:
            stdout = "ffmpeg version fixture --enable-libass --enable-libfontconfig\n"
        else:
            stdout = " T.C ass Render ASS subtitles\n T.C subtitles Render text subtitles\n"
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    report = check_media_capabilities(which=lambda name: f"/fixture/{name}", runner=runner)

    assert report.ready_for_rendering
    assert report.ass_filter
    assert report.subtitles_filter
    assert report.fontconfig
