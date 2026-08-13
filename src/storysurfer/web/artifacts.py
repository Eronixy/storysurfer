"""Public artifact allowlist and redacted manifest export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from storysurfer.domain import JsonValue
from storysurfer.storage import RunStorage

ARTIFACT_ALLOWLIST = {
    "preview.mp4",
    "final.mp4",
    "captions.ass",
    "captions.srt",
    "script.txt",
    "selection.json",
    "verification-preview.json",
    "verification.json",
}


def write_public_manifest(run_id: str, storage: RunStorage) -> Path:
    manifest = cast(
        dict[str, JsonValue], json.loads(json.dumps(storage.read_manifest(run_id)))
    )
    config = manifest.get("config")
    if isinstance(config, dict):
        reddit = config.get("reddit")
        if isinstance(reddit, dict):
            reddit.pop("user_agent", None)
    storage.write_json(run_id, "manifest-public.json", manifest)
    return storage.artifact_path(run_id, "manifest-public.json")
