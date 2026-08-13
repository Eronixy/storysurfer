"""Validated project configuration with environment-only secrets."""

from __future__ import annotations

import os
import re
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
class SpeechConfig:
    provider: str = "edge-tts"
    voice: str = "en-US-EmmaMultilingualNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    sample_rate: int = 24_000
    segment_pause_ms: int = 350
    connect_timeout_seconds: int = 10
    receive_timeout_seconds: int = 60
    pronunciations: tuple[tuple[str, str], ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.voice)


@dataclass(frozen=True, slots=True)
class AppConfig:
    reddit: RedditConfig
    selection: SelectionConfig
    storage: StorageConfig
    media: MediaConfig
    speech: SpeechConfig = SpeechConfig()

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
            "speech": {
                "provider": self.speech.provider,
                "configured": self.speech.configured,
                "voice": self.speech.voice,
                "rate": self.speech.rate,
                "volume": self.speech.volume,
                "pitch": self.speech.pitch,
                "sample_rate": self.speech.sample_rate,
                "segment_pause_ms": self.speech.segment_pause_ms,
                "connect_timeout_seconds": self.speech.connect_timeout_seconds,
                "receive_timeout_seconds": self.speech.receive_timeout_seconds,
                "pronunciations": [list(item) for item in self.speech.pronunciations],
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
    speech_data = _section(raw, "speech")
    legacy_speech_keys = {
        "api_key",
        "voice_id",
        "model_id",
        "request_timeout_seconds",
        "stability",
        "similarity_boost",
        "style",
        "use_speaker_boost",
    }.intersection(speech_data)
    if legacy_speech_keys:
        names = ", ".join(sorted(legacy_speech_keys))
        raise ConfigurationError(
            f"Obsolete ElevenLabs speech settings are present: {names}",
            hint="Use the Edge TTS voice, rate, volume, pitch, and timeout settings.",
        )
    provider = _string(speech_data, "provider", "edge-tts").lower().replace("_", "-")
    if provider != "edge-tts":
        raise ConfigurationError(f"Unsupported speech provider: {provider}")
    pronunciations = _pronunciations(speech_data.get("pronunciations", {}))
    return AppConfig(
        reddit=reddit,
        selection=selection,
        storage=StorageConfig(Path(_string(storage_data, "runs_dir", "runs"))),
        media=MediaConfig(
            ffmpeg_path=_string(media_data, "ffmpeg_path", "ffmpeg"),
            ffprobe_path=_string(media_data, "ffprobe_path", "ffprobe"),
        ),
        speech=SpeechConfig(
            provider=provider,
            voice=_string(speech_data, "voice", "en-US-EmmaMultilingualNeural"),
            rate=_speech_modifier(speech_data, "rate", "+0%", "%"),
            volume=_speech_modifier(speech_data, "volume", "+0%", "%"),
            pitch=_speech_modifier(speech_data, "pitch", "+0Hz", "Hz"),
            sample_rate=24_000,
            segment_pause_ms=_integer(
                speech_data, "segment_pause_ms", 350, minimum=0, maximum=5_000
            ),
            connect_timeout_seconds=_integer(
                speech_data, "connect_timeout_seconds", 10, minimum=1, maximum=120
            ),
            receive_timeout_seconds=_integer(
                speech_data, "receive_timeout_seconds", 60, minimum=5, maximum=300
            ),
            pronunciations=pronunciations,
        ),
    )


def _speech_modifier(
    data: Mapping[str, object], key: str, default: str, suffix: str
) -> str:
    value = _string(data, key, default)
    pattern = rf"^[+-]\d+{re.escape(suffix)}$"
    if not re.fullmatch(pattern, value):
        raise ConfigurationError(
            f"speech.{key} must use Edge TTS syntax such as {default!r}."
        )
    return value


def _pronunciations(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise ConfigurationError("speech.pronunciations must be a mapping.")
    result: list[tuple[str, str]] = []
    for source, spoken in value.items():
        if not isinstance(source, str) or not source.strip():
            raise ConfigurationError("Pronunciation source values must be non-empty strings.")
        if not isinstance(spoken, str) or not spoken.strip():
            raise ConfigurationError("Pronunciation replacements must be non-empty strings.")
        result.append((source.strip(), spoken.strip()))
    return tuple(sorted(result, key=lambda item: item[0].casefold()))
