from __future__ import annotations

import importlib.util
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

MODULE_PATH = Path(__file__).parents[2] / "download.py"
SPEC = importlib.util.spec_from_file_location("download_script", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
DOWNLOAD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOWNLOAD)
build_options = DOWNLOAD.build_options
download_video = DOWNLOAD.download_video
resolve_js_runtime = DOWNLOAD.resolve_js_runtime
validate_youtube_url = DOWNLOAD.validate_youtube_url


class FakeDownloader:
    def __init__(self, options: dict[str, Any], output: Path) -> None:
        self.options = options
        self.output = output
        self.requested_url: str | None = None

    def __enter__(self) -> FakeDownloader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def extract_info(self, url: str, *, download: bool) -> dict[str, str]:
        assert download
        self.requested_url = url
        return {"id": "video-id", "title": "Gameplay", "ext": "mp4"}

    def prepare_filename(self, info: dict[str, Any]) -> str:
        assert info["id"] == "video-id"
        return str(self.output)


def test_youtube_url_validation_accepts_videos_and_rejects_other_targets() -> None:
    assert validate_youtube_url("https://youtu.be/abcdefghijk")
    assert validate_youtube_url("https://www.youtube.com/watch?v=abcdefghijk")
    assert validate_youtube_url("https://youtube.com/shorts/abcdefghijk")

    with pytest.raises(ValueError):
        validate_youtube_url("https://youtube.example/watch?v=abcdefghijk")
    with pytest.raises(ValueError):
        validate_youtube_url("https://youtube.com/playlist?list=example")


def test_download_options_limit_format_and_disable_playlists(tmp_path: Path) -> None:
    options = build_options(
        tmp_path,
        720,
        which=lambda name: "/opt/node/bin/node" if name == "node" else None,
    )

    assert options["noplaylist"] is True
    assert options["overwrites"] is False
    assert options["merge_output_format"] == "mp4"
    assert "height<=720" in options["format"]
    assert str(tmp_path.resolve()) in options["outtmpl"]
    assert options["js_runtimes"] == {
        "node": {"path": "/opt/node/bin/node"}
    }


def test_js_runtime_detection_fails_before_downloading() -> None:
    with pytest.raises(ValueError, match="Install Deno or Node.js 22"):
        resolve_js_runtime(which=lambda _: None)


def test_download_requires_rights_and_uses_python_api(tmp_path: Path) -> None:
    calls: list[FakeDownloader] = []
    expected = tmp_path / "Gameplay [video-id].mp4"

    def factory(options: dict[str, Any]) -> FakeDownloader:
        downloader = FakeDownloader(options, expected)
        calls.append(downloader)
        return downloader

    with pytest.raises(ValueError, match="acknowledge-rights"):
        download_video(
            "https://youtu.be/abcdefghijk",
            tmp_path,
            1080,
            acknowledge_rights=False,
            which=lambda name: "/usr/bin/node" if name == "node" else None,
            downloader_factory=factory,
        )
    assert calls == []

    result = download_video(
        "https://youtu.be/abcdefghijk",
        tmp_path,
        1080,
        acknowledge_rights=True,
        which=lambda name: "/usr/bin/node" if name == "node" else None,
        downloader_factory=factory,
    )
    assert result == expected
    assert calls[0].requested_url == "https://youtu.be/abcdefghijk"
