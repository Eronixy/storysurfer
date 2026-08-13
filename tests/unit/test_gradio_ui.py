from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from storysurfer.config import AppConfig, StorageConfig, WebConfig
from storysurfer.errors import WebError
from storysurfer.gradio_ui import (
    _copy_upload,
    _SessionTokens,
    _validate_upload,
    create_gradio_app,
)


def test_gradio_ui_exposes_private_complete_workflow(
    app_config: AppConfig, tmp_path: Path
) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    config = replace(
        app_config,
        storage=StorageConfig(tmp_path / "runs"),
        web=WebConfig(database_path=tmp_path / "jobs.sqlite3"),
    )
    application = create_gradio_app(
        config, start_worker=False, upload_root=uploads
    )
    browser = application.demo.get_config_file()
    labels = {item["props"].get("label") for item in browser["components"]}

    assert "Reddit post URL" in labels
    assert "Narration language / locale" in labels
    assert "Edge TTS voice" in labels
    assert "Include complete exchanges" in labels
    assert "I confirm I have rights to use the source content and assets" in labels
    assert {item["api_visibility"] for item in browser["dependencies"]} == {"private"}
    tabs = [item for item in browser["components"] if item["type"] == "tabs"]
    tab_items = [item for item in browser["components"] if item["type"] == "tabitem"]
    assert tabs[0]["props"]["elem_classes"] == ["ss-tabs"]
    assert len(tab_items) == 4
    assert all(
        item["props"]["elem_classes"] == ["ss-tab-content"] for item in tab_items
    )
    voice_controls = [
        item
        for item in browser["components"]
        if item["props"].get("label") == "Edge TTS voice"
    ]
    assert len(voice_controls) == 2
    assert {item["type"] for item in voice_controls} == {"dropdown"}


def test_upload_is_contained_size_limited_and_copied_atomically(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")
    with pytest.raises(WebError, match="protected directory"):
        _validate_upload(outside, uploads, 100)

    large = uploads / "large.mp4"
    large.write_bytes(b"x" * 101)
    with pytest.raises(WebError, match="size limit"):
        _validate_upload(large, uploads, 100)

    source = uploads / "gameplay.mp4"
    source.write_bytes(b"valid synthetic video")
    destination = tmp_path / "run" / "staging" / "gameplay.mp4"
    destination.parent.mkdir(parents=True)
    _copy_upload(_validate_upload(source, uploads, 100), destination, 100)
    assert destination.read_bytes() == source.read_bytes()
    assert list(destination.parent.glob(".gameplay.mp4.*")) == []


def test_state_changing_action_token_is_bound_to_session() -> None:
    sessions = _SessionTokens()
    token = sessions.issue("browser-one")
    sessions.verify(token, "browser-one")
    with pytest.raises(WebError, match="CSRF"):
        sessions.verify(token, "browser-two")
