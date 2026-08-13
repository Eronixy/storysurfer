"""Command-line interface for foundation and editorial stages."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from redditsurfer.config import AppConfig, load_config
from redditsurfer.errors import RedditSurferError
from redditsurfer.media.capabilities import check_media_capabilities
from redditsurfer.pipeline import caption_run, ingest, narrate, preview, script_run, select
from redditsurfer.storage import RunStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redditsurfer",
        description="Create source-linked narrated videos from public Reddit stories.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check credentials and media capabilities")
    _add_config_argument(doctor)

    ingest_parser = subparsers.add_parser("ingest", help="fetch and snapshot a Reddit thread")
    ingest_parser.add_argument("source", help="Reddit submission URL or ID")
    ingest_parser.add_argument("--run-id", help=argparse.SUPPRESS)
    _add_config_argument(ingest_parser)

    select_parser = subparsers.add_parser("select", help="rank and select story comments")
    select_parser.add_argument("run_id", help="existing run ID")
    _add_config_argument(select_parser)

    script_parser = subparsers.add_parser("script", help="create a source-linked narration script")
    script_parser.add_argument("run_id", help="existing run ID")
    _add_config_argument(script_parser)

    narrate_parser = subparsers.add_parser(
        "narrate", help="synthesize narration audio and word timestamps"
    )
    narrate_parser.add_argument("run_id", help="existing run ID")
    _add_config_argument(narrate_parser)

    caption_parser = subparsers.add_parser(
        "caption", help="create word-timed animated ASS and plain SRT captions"
    )
    caption_parser.add_argument("run_id", help="existing run ID")
    _add_config_argument(caption_parser)

    preview_parser = subparsers.add_parser(
        "preview", help="render a vertical preview using local gameplay"
    )
    preview_parser.add_argument("run_id", help="existing run ID")
    preview_parser.add_argument(
        "--background", required=True, type=Path, help="licensed local gameplay video"
    )
    preview_parser.add_argument(
        "--preset", choices=("subway", "minecraft"), default="minecraft"
    )
    preview_parser.add_argument(
        "--crop-offset",
        type=float,
        default=0.0,
        help="normalized horizontal (subway) or vertical (minecraft) crop offset, -1 to 1",
    )
    _add_config_argument(preview_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        config = load_config(arguments.config)
        if arguments.command == "doctor":
            return _doctor(config)
        storage = RunStorage(config.storage.runs_dir)
        if arguments.command == "ingest":
            run_id, snapshot = ingest(
                arguments.source,
                config,
                storage,
                run_id=arguments.run_id,
            )
            print(f"Run: {run_id}")
            print(f"Stored {len(snapshot.comments)} comments in {storage.run_dir(run_id)}")
            print(f"Next: uv run redditsurfer select {run_id}")
            return 0
        if arguments.command == "select":
            result = select(arguments.run_id, config, storage)
            selected = [candidate for candidate in result.candidates if candidate.selected]
            exchanges = sum(candidate.kind == "op_exchange" for candidate in selected)
            print(f"Run: {arguments.run_id}")
            print(
                f"Selected {len(selected)} candidates ({exchanges} OP exchanges), "
                f"{result.selected_comment_words}/{result.comment_word_budget} comment words"
            )
            for warning in result.warnings:
                print(f"Warning: {warning}")
            print(f"Artifact: {storage.artifact_path(arguments.run_id, 'selection.json')}")
            return 0
        if arguments.command == "script":
            script = script_run(arguments.run_id, config, storage)
            print(f"Run: {arguments.run_id}")
            print(
                f"Created {len(script.segments)} source-linked segments; "
                f"estimated duration {script.estimated_duration_ms / 1000:.1f}s"
            )
            report_path = storage.artifact_path(arguments.run_id, "script.txt")
            print(f"Review before network synthesis: {report_path}")
            print(f"Next: uv run redditsurfer narrate {arguments.run_id}")
            return 0
        if arguments.command == "narrate":
            speech = narrate(arguments.run_id, config, storage)
            print(f"Run: {arguments.run_id}")
            print(
                f"Created {speech.duration_ms / 1000:.1f}s narration with "
                f"{len(speech.words)} timed words"
            )
            print(f"Audio: {storage.internal_path(arguments.run_id, speech.audio_path)}")
            print(f"Timing: {storage.artifact_path(arguments.run_id, 'speech.json')}")
            print(f"Next: uv run redditsurfer caption {arguments.run_id}")
            return 0
        if arguments.command == "caption":
            captions = caption_run(arguments.run_id, config, storage)
            print(f"Run: {arguments.run_id}")
            print(
                f"Created {len(captions.cues)} word-timed cues through "
                f"{captions.duration_ms / 1000:.1f}s"
            )
            print(f"ASS: {storage.artifact_path(arguments.run_id, captions.ass_path)}")
            print(f"SRT: {storage.artifact_path(arguments.run_id, captions.srt_path)}")
            print(
                "Next: uv run redditsurfer preview "
                f"{arguments.run_id} --background /path/to/gameplay.mp4"
            )
            return 0
        if arguments.command == "preview":
            timeline = preview(
                arguments.run_id,
                arguments.background,
                arguments.preset,
                config,
                storage,
                crop_offset=arguments.crop_offset,
            )
            print(f"Run: {arguments.run_id}")
            print(
                f"Rendered {timeline.output_width}x{timeline.output_height} "
                f"{timeline.frame_rate}fps preview ({timeline.duration_ms / 1000:.1f}s)"
            )
            print(f"Video: {storage.artifact_path(arguments.run_id, 'preview.mp4')}")
            return 0
    except RedditSurferError as exc:
        print(f"Error: {exc.display()}", file=sys.stderr)
        return exc.exit_code
    parser.error("unsupported command")
    return 2


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="YAML configuration path")


def _doctor(config: AppConfig) -> int:
    report = check_media_capabilities(
        config.media.ffmpeg_path,
        config.media.ffprobe_path,
    )
    reddit_status = "configured" if config.reddit.credentials_configured else "missing credentials"
    speech_status = f"voice {config.speech.voice}" if config.speech.configured else "missing voice"
    print(f"Reddit API: {reddit_status}")
    print(f"Speech provider ({config.speech.provider}): {speech_status}")
    print(f"FFmpeg: {report.ffmpeg_version or 'not found'}")
    print(f"ffprobe: {'found' if report.ffprobe_found else 'not found'}")
    print(f"ASS captions: {'ready' if report.ass_filter and report.fontconfig else 'not ready'}")
    for problem in report.problems:
        print(f"Problem: {problem}")
    if not config.reddit.credentials_configured:
        print("Problem: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET for live ingestion.")
    if not config.speech.configured:
        print("Problem: set speech.voice to an Edge TTS voice name.")
    return (
        0
        if report.ready_for_rendering
        and config.reddit.credentials_configured
        and config.speech.configured
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
