from __future__ import annotations

import json
from pathlib import Path

import pytest

from storysurfer.config import (
    AppConfig,
    MediaConfig,
    RedditConfig,
    SelectionConfig,
    StorageConfig,
)
from storysurfer.domain import ThreadSnapshot


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        reddit=RedditConfig(
            client_id="test-client",
            client_secret="test-secret",
            user_agent="linux:storysurfer:test (synthetic)",
            author_hash_salt="test-salt",
        ),
        selection=SelectionConfig(target_duration_seconds=90),
        storage=StorageConfig(tmp_path / "runs"),
        media=MediaConfig(),
    )


@pytest.fixture
def thread_snapshot() -> ThreadSnapshot:
    path = Path(__file__).parent / "fixtures" / "thread.json"
    return ThreadSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
