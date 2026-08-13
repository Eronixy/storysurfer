"""Animated ASS subtitle export with word-timed karaoke highlighting."""

from __future__ import annotations

from storysurfer.config import CaptionConfig, MediaConfig
from storysurfer.domain import CaptionArtifact, CaptionCue
from storysurfer.errors import CaptionError


def render_ass(
    captions: CaptionArtifact,
    config: CaptionConfig,
    media: MediaConfig,
) -> str:
    if any(character in config.font_name for character in ",\r\n"):
        raise CaptionError("Caption font name cannot contain commas or line breaks.")
    if config.margin_horizontal * 2 >= media.output_width:
        raise CaptionError("Caption horizontal margins leave no usable text area.")
    if config.margin_bottom >= media.output_height:
        raise CaptionError("Caption bottom margin is outside the output frame.")
    styles = "\n".join(
        _style_line(name, config, scale)
        for name, scale in (
            ("Title", 1.08),
            ("Story", 1.0),
            ("Commenter", 0.96),
            ("OP", 0.96),
        )
    )
    events = "\n".join(_dialogue(cue, config) for cue in captions.cues)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {media.output_width}\n"
        f"PlayResY: {media.output_height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"{styles}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        f"{events}\n"
    )


def _style_line(name: str, config: CaptionConfig, scale: float) -> str:
    size = round(config.font_size * scale)
    primary = _ass_color(config.highlight_color)
    secondary = _ass_color(config.primary_color)
    outline = _ass_color(config.outline_color)
    return (
        f"Style: {name},{config.font_name},{size},{primary},{secondary},{outline},"
        "&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{config.outline_size},2,2,{config.margin_horizontal},"
        f"{config.margin_horizontal},{config.margin_bottom},1"
    )


def _dialogue(cue: CaptionCue, config: CaptionConfig) -> str:
    animation = (
        "{\\an2\\q2\\fscx82\\fscy82\\alpha&H28&"
        f"\\t(0,{config.pop_ms},\\fscx100\\fscy100\\alpha&H00&)}}"
    )
    text = _karaoke_text(cue)
    return (
        f"Dialogue: 0,{_timestamp(cue.start_ms)},{_timestamp(cue.end_ms)},"
        f"{_style_name(cue.style)},,0,0,0,,{animation}{text}"
    )


def _karaoke_text(cue: CaptionCue) -> str:
    result: list[str] = []
    line_break_at = _line_break_index(cue)
    cursor = cue.start_ms
    for index, word in enumerate(cue.words):
        if index:
            result.append("\\N" if index == line_break_at else " ")
        gap = max(0, word.start_ms - cursor)
        if gap:
            result.append(f"{{\\k{max(1, round(gap / 10))}}}")
        duration = max(1, round((word.end_ms - word.start_ms) / 10))
        result.append(f"{{\\kf{duration}}}{_escape_text(word.text)}")
        cursor = word.end_ms
    return "".join(result)


def _line_break_index(cue: CaptionCue) -> int | None:
    if "\n" not in cue.text:
        return None
    first_line = cue.text.split("\n", 1)[0]
    return len(first_line.split())


def _style_name(style: str) -> str:
    return {
        "title": "Title",
        "story": "Story",
        "commenter": "Commenter",
        "op": "OP",
    }[style]


def _escape_text(value: str) -> str:
    return (
        value.replace("\\", "＼")
        .replace("{", "｛")
        .replace("}", "｝")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _ass_color(rgb: str) -> str:
    red, green, blue = rgb[1:3], rgb[3:5], rgb[5:7]
    return f"&H00{blue}{green}{red}"


def _timestamp(milliseconds: int) -> str:
    centiseconds = round(milliseconds / 10)
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"
