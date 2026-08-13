"""Command-line interface for foundation and editorial stages."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from redditsurfer.config import AppConfig, load_config
from redditsurfer.domain import ThreadSnapshot
from redditsurfer.errors import ConfigurationError, RedditSurferError
from redditsurfer.media import media_for_profile, probe_media
from redditsurfer.media.capabilities import check_media_capabilities
from redditsurfer.pipeline import (
    build,
    caption_run,
    ingest,
    narrate,
    preview,
    render_final,
    script_run,
    select,
    verify,
)
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
    _add_render_arguments(preview_parser)
    _add_config_argument(preview_parser)

    render_parser = subparsers.add_parser(
        "render", help="render rights-gated full-resolution final.mp4"
    )
    render_parser.add_argument("run_id", help="existing run ID")
    _add_render_arguments(render_parser)
    render_parser.add_argument(
        "--acknowledge-rights",
        action="store_true",
        help="confirm that source content and all creative assets may be used",
    )
    _add_config_argument(render_parser)

    verify_parser = subparsers.add_parser(
        "verify", help="verify rendered streams, timing, captions, and artifacts"
    )
    verify_parser.add_argument("run_id", help="existing run ID")
    verify_parser.add_argument(
        "--profile", choices=("preview", "final"), default="final"
    )
    _add_config_argument(verify_parser)

    build_parser = subparsers.add_parser(
        "build", help="run or resume the complete pipeline"
    )
    build_parser.add_argument("source", nargs="?", help="Reddit submission URL or ID")
    build_parser.add_argument(
        "--thread-file", type=Path, help="normalized cached thread.json instead of Reddit"
    )
    build_parser.add_argument("--resume", metavar="RUN_ID", help="resume an existing run")
    build_parser.add_argument(
        "--profile", choices=("preview", "final"), default="preview"
    )
    build_parser.add_argument(
        "--acknowledge-rights",
        action="store_true",
        help="required when --profile final is selected",
    )
    build_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="probe local inputs and show the plan without Reddit, TTS, or rendering",
    )
    _add_render_arguments(build_parser)
    _add_config_argument(build_parser)
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
            selection_result = select(arguments.run_id, config, storage)
            selected = [
                candidate
                for candidate in selection_result.candidates
                if candidate.selected
            ]
            exchanges = sum(candidate.kind == "op_exchange" for candidate in selected)
            print(f"Run: {arguments.run_id}")
            print(
                f"Selected {len(selected)} candidates ({exchanges} OP exchanges), "
                f"{selection_result.selected_comment_words}/"
                f"{selection_result.comment_word_budget} comment words"
            )
            for warning in selection_result.warnings:
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
            report = verify(arguments.run_id, "preview", config, storage)
            print(f"Run: {arguments.run_id}")
            print(
                f"Rendered {timeline.output_width}x{timeline.output_height} "
                f"{timeline.frame_rate}fps preview ({timeline.duration_ms / 1000:.1f}s)"
            )
            print(f"Video: {storage.artifact_path(arguments.run_id, 'preview.mp4')}")
            print(f"Verification: {'passed' if report.passed else 'failed'}")
            return 0
        if arguments.command == "render":
            timeline = render_final(
                arguments.run_id,
                arguments.background,
                arguments.preset,
                config,
                storage,
                acknowledge_rights=arguments.acknowledge_rights,
                crop_offset=arguments.crop_offset,
            )
            report = verify(arguments.run_id, "final", config, storage)
            print(f"Run: {arguments.run_id}")
            print(
                f"Rendered {timeline.output_width}x{timeline.output_height} "
                f"{timeline.frame_rate}fps final ({timeline.duration_ms / 1000:.1f}s)"
            )
            print(f"Video: {storage.artifact_path(arguments.run_id, 'final.mp4')}")
            print(f"Verification: {'passed' if report.passed else 'failed'}")
            return 0
        if arguments.command == "verify":
            report = verify(arguments.run_id, arguments.profile, config, storage)
            print(f"Run: {arguments.run_id}")
            print(f"{arguments.profile.title()} verification passed")
            for check in report.checks:
                print(f"  {check.name}: {'pass' if check.passed else 'fail'} ({check.message})")
            return 0
        if arguments.command == "build":
            cached_thread = (
                _load_cached_thread(arguments.thread_file)
                if arguments.thread_file is not None
                else None
            )
            _validate_build_sources(
                arguments.source, cached_thread, arguments.resume
            )
            if arguments.dry_run:
                return _dry_run(arguments, config, storage)
            build_result = build(
                arguments.background,
                arguments.preset,
                config,
                storage,
                source_value=arguments.source,
                cached_thread=cached_thread,
                resume_run_id=arguments.resume,
                profile=arguments.profile,
                acknowledge_rights=arguments.acknowledge_rights,
                crop_offset=arguments.crop_offset,
            )
            print(f"Run: {build_result.run_id}")
            print(f"Profile: {build_result.profile}")
            video_name = f"{build_result.profile}.mp4"
            print(f"Video: {storage.artifact_path(build_result.run_id, video_name)}")
            report_name = (
                "verification.json"
                if build_result.profile == "final"
                else "verification-preview.json"
            )
            print(
                f"Verification: {storage.artifact_path(build_result.run_id, report_name)}"
            )
            return 0
    except RedditSurferError as exc:
        print(f"Error: {exc.display()}", file=sys.stderr)
        return exc.exit_code
    parser.error("unsupported command")
    return 2


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="YAML configuration path")


def _add_render_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--background", required=True, type=Path, help="licensed local gameplay video"
    )
    parser.add_argument(
        "--preset", choices=("subway", "minecraft"), default="minecraft"
    )
    parser.add_argument(
        "--crop-offset",
        type=float,
        default=0.0,
        help="normalized horizontal (subway) or vertical (minecraft) crop offset, -1 to 1",
    )


def _load_cached_thread(path: Path) -> ThreadSnapshot:
    try:
        return ThreadSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Cached thread file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigurationError(f"Cached thread file is invalid: {path}") from exc


def _validate_build_sources(
    source: str | None,
    cached_thread: ThreadSnapshot | None,
    resume_run_id: str | None,
) -> None:
    supplied = sum(value is not None for value in (source, cached_thread, resume_run_id))
    if supplied != 1:
        raise ConfigurationError(
            "Build requires exactly one SOURCE, --thread-file, or --resume RUN_ID."
        )


def _dry_run(arguments: argparse.Namespace, config: AppConfig, storage: RunStorage) -> int:
    info = probe_media(arguments.background, config.media.ffprobe_path)
    media = media_for_profile(config.media, arguments.profile)
    print("Dry run: no Reddit, Edge TTS, or FFmpeg render work will be performed.")
    if arguments.resume:
        print(f"Run: {arguments.resume}")
        statuses = storage.stage_statuses(arguments.resume)
        print(
            "Existing stages: "
            + (", ".join(f"{name}={status}" for name, status in statuses.items()) or "none")
        )
    elif arguments.thread_file:
        print(f"Reddit input: cached {arguments.thread_file}")
    else:
        print(f"Reddit input: live {arguments.source}")
    print(f"Speech: {config.speech.provider} / {config.speech.voice}")
    print(
        f"Gameplay: {info.width}x{info.height}, {info.duration_ms / 1000:.1f}s, "
        f"audio={'yes' if info.has_audio else 'no'}"
    )
    print(
        f"Output: {arguments.profile} {media.output_width}x{media.output_height} "
        f"at {media.frame_rate}fps"
    )
    if arguments.profile == "final" and not arguments.acknowledge_rights:
        print("Blocked final step: pass --acknowledge-rights after reviewing sources/assets.")
    return 0


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
