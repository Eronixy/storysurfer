from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from storysurfer.config import AppConfig, StorageConfig, WebConfig
from storysurfer.errors import WebError
from storysurfer.gradio_ui import (
    CSS,
    GradioApplication,
    _centralized_script_text,
    _copy_upload,
    _parse_centralized_script,
    _selected_candidate_ids,
    _SessionTokens,
    _validate_upload,
    create_gradio_app,
)


def test_gradio_launch_applies_css(app_config: AppConfig) -> None:
    launch_options: dict[str, object] = {}
    demo = SimpleNamespace(launch=lambda **options: launch_options.update(options))
    worker = SimpleNamespace(start=lambda: None, stop=lambda: None)
    application = GradioApplication(demo=demo, worker=worker, config=app_config)

    application.launch("127.0.0.1", 7860, False)

    assert launch_options["css"] == CSS
    assert launch_options["server_name"] == "127.0.0.1"
    assert launch_options["server_port"] == 7860


def test_gradio_ui_exposes_private_complete_workflow(app_config: AppConfig, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    config = replace(
        app_config,
        storage=StorageConfig(tmp_path / "runs"),
        web=WebConfig(database_path=tmp_path / "jobs.sqlite3"),
    )
    application = create_gradio_app(config, start_worker=False, upload_root=uploads)
    browser = application.demo.get_config_file()
    labels = {item["props"].get("label") for item in browser["components"]}

    assert "Reddit post URL" in labels
    assert "Narration language / locale" in labels
    assert "Edge TTS voice" in labels
    assert "Update post URLs (optional, one per line)" in labels
    assert "Requested OP exchanges" in labels
    assert "Requested standalone comments" in labels
    assert "Centralized narration script" in labels
    assert "Relevant comment candidates — edit Included only" in labels
    button_values = {
        item["props"].get("value")
        for item in browser["components"]
        if item["type"] == "button"
    }
    assert "Delete selected project" in button_values
    assert "Permanently delete project" in button_values
    delete_modals = [
        item
        for item in browser["components"]
        if item["props"].get("elem_id") == "delete-project-modal"
    ]
    assert len(delete_modals) == 1
    assert not delete_modals[0]["props"]["visible"]
    # Gradio 6 applies CSS at launch time instead of storing it in the Blocks config.
    assert browser["css"] is None
    assert "#delete-project-dialog" in CSS
    assert "#confirm-delete-project" in CSS
    assert "height: auto !important" in CSS
    assert "flex: 0 0 auto !important" in CSS
    assert "I confirm I have rights to use the source content and assets" in labels
    assert {item["api_visibility"] for item in browser["dependencies"]} == {"private"}
    tabs = [item for item in browser["components"] if item["type"] == "tabs"]
    tab_items = [item for item in browser["components"] if item["type"] == "tabitem"]
    assert tabs[0]["props"]["elem_classes"] == ["ss-tabs"]
    assert len(tab_items) == 4
    assert all(item["props"]["elem_classes"] == ["ss-tab-content"] for item in tab_items)
    voice_controls = [
        item for item in browser["components"] if item["props"].get("label") == "Edge TTS voice"
    ]
    assert len(voice_controls) == 2
    assert {item["type"] for item in voice_controls} == {"dropdown"}
    components = {item["id"]: item for item in browser["components"]}
    candidate_table = next(
        item
        for item in browser["components"]
        if item["props"].get("label") == "Relevant comment candidates — edit Included only"
    )
    assert candidate_table["props"]["interactive"]
    assert candidate_table["props"]["static_columns"] == [0, 2, 3, 4, 5]
    assert any(
        dependency["targets"][0][1] == "input"
        and candidate_table["id"] in dependency["inputs"]
        for dependency in browser["dependencies"]
    )
    locale_filter_events = [
        dependency["targets"][0][1]
        for dependency in browser["dependencies"]
        if any(
            components[component_id]["props"].get("label") == "Narration language / locale"
            for component_id in dependency["inputs"]
        )
    ]
    assert locale_filter_events == ["input", "input"]


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


def test_centralized_script_round_trips_segment_text_and_protects_markers() -> None:
    script = SimpleNamespace(
        segments=(
            SimpleNamespace(id="title-one", spoken_text="First title"),
            SimpleNamespace(id="post-one", spoken_text="Full post text"),
        )
    )
    value = _centralized_script_text(script)

    assert _parse_centralized_script(value, script) == {
        "title-one": "First title",
        "post-one": "Full post text",
    }
    with pytest.raises(WebError, match="markers"):
        _parse_centralized_script(value.replace("[[post-one]]", "[[changed]]"), script)


def test_selected_candidate_ids_uses_checked_table_rows() -> None:
    rows = [
        ["candidate-1", True, "comment"],
        ["candidate-2", False, "comment"],
        ["candidate-3", True, "op_exchange"],
    ]

    assert _selected_candidate_ids(rows) == {"candidate-1", "candidate-3"}
