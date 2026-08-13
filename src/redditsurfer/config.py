"""Validated project configuration with environment-only secrets."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from redditsurfer.domain import JsonValue
from redditsurfer.errors import ConfigurationError


def _section(data: Mapping[str, object], name: str) -> dict[str, object]:
    value = data.get(name, {})
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"Configuration section '{name}' must be a mapping.")
    return cast(dict[str, object], value)


def _string(data: Mapping[str, object], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Configuration value '{key}' must be a non-empty string.")
    return value.strip()


def _integer(
    data: Mapping[str, object], key: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ConfigurationError(
            f"Configuration value '{key}' must be an integer from {minimum} to {maximum}."
        )
    return value


def _fraction(data: Mapping[str, object], key: str, default: float) -> float:
    value = data.get(key, default)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ConfigurationError(f"Configuration value '{key}' must be a number.")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ConfigurationError(f"Configuration value '{key}' must be between 0 and 1.")
    return result


@dataclass(frozen=True, slots=True)
class RedditConfig:
    client_id: str | None
    client_secret: str | None
    user_agent: str
    comment_sort: str = "best"
    max_comments: int = 250
    replace_more_limit: int = 8
    author_hash_salt: str = "redditsurfer-local"

    @property
    def credentials_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    target_duration_seconds: int = 75
    words_per_minute: int = 165
    comment_budget_fraction: float = 0.25
    max_comment_depth: int = 8
    max_candidate_words: int = 220
    max_selected_candidates: int = 5


@dataclass(frozen=True, slots=True)
class StorageConfig:
    runs_dir: Path = Path("runs")


@dataclass(frozen=True, slots=True)
class MediaConfig:
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"


@dataclass(frozen=True, slots=True)
class AppConfig:
    reddit: RedditConfig
    selection: SelectionConfig
    storage: StorageConfig
    media: MediaConfig

    def public_dict(self) -> dict[str, JsonValue]:
        return {
            "reddit": {
                "credentials_configured": self.reddit.credentials_configured,
                "user_agent": self.reddit.user_agent,
                "comment_sort": self.reddit.comment_sort,
                "max_comments": self.reddit.max_comments,
                "replace_more_limit": self.reddit.replace_more_limit,
            },
            "selection": {
                "target_duration_seconds": self.selection.target_duration_seconds,
                "words_per_minute": self.selection.words_per_minute,
                "comment_budget_fraction": self.selection.comment_budget_fraction,
                "max_comment_depth": self.selection.max_comment_depth,
                "max_candidate_words": self.selection.max_candidate_words,
                "max_selected_candidates": self.selection.max_selected_candidates,
            },
            "storage": {"runs_dir": str(self.storage.runs_dir)},
            "media": {
                "ffmpeg_path": self.media.ffmpeg_path,
                "ffprobe_path": self.media.ffprobe_path,
            },
        }


def load_config(
    path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> AppConfig:
    """Load YAML configuration and override secrets/user agent from the environment."""
    environment = os.environ if environ is None else environ
    config_path = path
    if config_path is None:
        default_path = Path("config.yaml")
        config_path = default_path if default_path.exists() else None

    raw: dict[str, object] = {}
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigurationError(f"Configuration file does not exist: {config_path}")
        try:
            loaded: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(f"Could not read configuration: {config_path}") from exc
        if loaded is not None:
            if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
                raise ConfigurationError("Configuration root must be a mapping.")
            raw = cast(dict[str, object], loaded)

    reddit_data = _section(raw, "reddit")
    forbidden_secrets = {"client_id", "client_secret"}.intersection(reddit_data)
    if forbidden_secrets:
        names = ", ".join(sorted(forbidden_secrets))
        raise ConfigurationError(
            f"Reddit secrets are environment-only; remove from config: {names}",
            hint="Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET instead.",
        )
    comment_sort = _string(reddit_data, "comment_sort", "best").lower()
    if comment_sort not in {"best", "top", "new", "old", "controversial", "qa"}:
        raise ConfigurationError("reddit.comment_sort is not supported.")
    user_agent = environment.get("REDDIT_USER_AGENT") or _string(
        reddit_data, "user_agent", "linux:redditsurfer:0.1.0 (development)"
    )
    reddit = RedditConfig(
        client_id=environment.get("REDDIT_CLIENT_ID") or None,
        client_secret=environment.get("REDDIT_CLIENT_SECRET") or None,
        user_agent=user_agent,
        comment_sort=comment_sort,
        max_comments=_integer(
            reddit_data, "max_comments", 250, minimum=1, maximum=2_000
        ),
        replace_more_limit=_integer(
            reddit_data, "replace_more_limit", 8, minimum=0, maximum=100
        ),
        author_hash_salt=environment.get(
            "REDDITSURFER_AUTHOR_SALT", "redditsurfer-local"
        ),
    )

    selection_data = _section(raw, "selection")
    selection = SelectionConfig(
        target_duration_seconds=_integer(
            selection_data,
            "target_duration_seconds",
            75,
            minimum=15,
            maximum=3_600,
        ),
        words_per_minute=_integer(
            selection_data, "words_per_minute", 165, minimum=80, maximum=300
        ),
        comment_budget_fraction=_fraction(
            selection_data, "comment_budget_fraction", 0.25
        ),
        max_comment_depth=_integer(
            selection_data, "max_comment_depth", 8, minimum=0, maximum=50
        ),
        max_candidate_words=_integer(
            selection_data, "max_candidate_words", 220, minimum=10, maximum=2_000
        ),
        max_selected_candidates=_integer(
            selection_data, "max_selected_candidates", 5, minimum=0, maximum=50
        ),
    )

    storage_data = _section(raw, "storage")
    media_data = _section(raw, "media")
    return AppConfig(
        reddit=reddit,
        selection=selection,
        storage=StorageConfig(Path(_string(storage_data, "runs_dir", "runs"))),
        media=MediaConfig(
            ffmpeg_path=_string(media_data, "ffmpeg_path", "ffmpeg"),
            ffprobe_path=_string(media_data, "ffprobe_path", "ffprobe"),
        ),
    )
