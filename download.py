"""Download one rights-cleared YouTube gameplay video with yt-dlp."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
JS_RUNTIMES = {
    "deno": "deno",
    "node": "node",
    "quickjs": "qjs",
    "bun": "bun",
}


class Downloader(Protocol):
    def extract_info(self, url: str, *, download: bool) -> Any: ...

    def prepare_filename(self, info: Mapping[str, Any]) -> str: ...


DownloaderFactory = Callable[[dict[str, Any]], AbstractContextManager[Downloader]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download one rights-cleared YouTube gameplay video."
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("downloads/gameplay"),
        help="destination directory (default: downloads/gameplay)",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=1080,
        choices=(360, 480, 720, 1080, 1440, 2160),
        help="maximum video height (default: 1080)",
    )
    parser.add_argument(
        "--acknowledge-rights",
        action="store_true",
        help="confirm you may download and reuse this video",
    )
    parser.add_argument(
        "--js-runtime",
        choices=("auto", *JS_RUNTIMES),
        default="auto",
        help="JavaScript runtime for YouTube challenges (default: auto)",
    )
    return parser


def validate_youtube_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
        raise ValueError("URL must be an HTTP(S) YouTube video URL.")
    parts = [part for part in parsed.path.split("/") if part]
    short_url = host == "youtu.be" and len(parts) == 1
    watch_url = parts == ["watch"] and bool(parse_qs(parsed.query).get("v"))
    path_url = len(parts) == 2 and parts[0] in {"embed", "live", "shorts"}
    if not (short_url or watch_url or path_url):
        raise ValueError("URL must identify a YouTube video.")
    return value.strip()


def build_options(
    output_dir: Path,
    max_height: int,
    *,
    js_runtime: str = "auto",
    which: Callable[[str], str | None] = shutil.which,
    progress_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
) -> dict[str, Any]:
    destination = output_dir.expanduser().resolve()
    return {
        "format": (
            f"bestvideo*[height<={max_height}]+bestaudio/"
            f"best[height<={max_height}]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(destination / "%(title).120s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "overwrites": False,
        "continuedl": True,
        "js_runtimes": resolve_js_runtime(js_runtime, which=which),
        "writethumbnail": False,
        "writeinfojson": False,
        "writesubtitles": False,
        "progress_hooks": progress_hooks or [],
        "postprocessor_hooks": progress_hooks or [],
    }


def resolve_js_runtime(
    requested: str = "auto",
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, dict[str, str]]:
    candidates = tuple(JS_RUNTIMES) if requested == "auto" else (requested,)
    for runtime in candidates:
        executable = JS_RUNTIMES.get(runtime)
        if executable is None:
            raise ValueError(f"Unsupported JavaScript runtime: {runtime}")
        path = which(executable)
        if path:
            return {runtime: {"path": str(Path(path).resolve())}}
    names = ", ".join(JS_RUNTIMES[name] for name in candidates)
    raise ValueError(
        f"No supported JavaScript runtime found ({names}). Install Deno or Node.js 22+."
    )


def download_video(
    url: str,
    output_dir: Path,
    max_height: int,
    *,
    acknowledge_rights: bool,
    js_runtime: str = "auto",
    which: Callable[[str], str | None] = shutil.which,
    downloader_factory: DownloaderFactory = YoutubeDL,
) -> Path:
    if not acknowledge_rights:
        raise ValueError(
            "Downloading requires --acknowledge-rights. Only download footage you may reuse."
        )
    source_url = validate_youtube_url(url)
    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    completed: list[Path] = []

    def progress(data: dict[str, Any]) -> None:
        if data.get("status") != "finished":
            return
        filename = data.get("filename")
        info = data.get("info_dict")
        if not isinstance(filename, str) and isinstance(info, Mapping):
            filepath = info.get("filepath")
            filename = filepath if isinstance(filepath, str) else None
        if isinstance(filename, str):
            path = Path(filename).resolve()
            if path.is_relative_to(destination):
                completed.append(path)

    options = build_options(
        destination,
        max_height,
        js_runtime=js_runtime,
        which=which,
        progress_hooks=[progress],
    )
    with downloader_factory(options) as downloader:
        info = downloader.extract_info(source_url, download=True)
        if not isinstance(info, Mapping) or info.get("_type") == "playlist":
            raise ValueError("The URL did not resolve to one downloadable video.")
        prepared = Path(downloader.prepare_filename(info)).resolve()

    for candidate in reversed(completed):
        if candidate.exists():
            return candidate
    if prepared.is_relative_to(destination):
        return prepared
    raise ValueError("yt-dlp returned an output path outside the destination directory.")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        path = download_video(
            arguments.url,
            arguments.output_dir,
            arguments.max_height,
            acknowledge_rights=arguments.acknowledge_rights,
            js_runtime=arguments.js_runtime,
        )
    except (DownloadError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Downloaded gameplay: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
