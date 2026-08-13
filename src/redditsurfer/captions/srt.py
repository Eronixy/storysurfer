"""Plain SRT subtitle export."""

from __future__ import annotations

from redditsurfer.domain import CaptionArtifact


def render_srt(captions: CaptionArtifact) -> str:
    blocks = [
        f"{index}\n{_timestamp(cue.start_ms)} --> {_timestamp(cue.end_ms)}\n{cue.text}\n"
        for index, cue in enumerate(captions.cues, start=1)
    ]
    return "\n".join(blocks)


def _timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
