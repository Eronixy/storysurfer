"""Validated, secret-free settings persisted for each browser-created run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, cast

from storysurfer.config import AppConfig
from storysurfer.domain import JsonValue
from storysurfer.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    source_url: str
    staging_path: str
    background_path: str
    preset: Literal["subway", "minecraft"]
    target_duration_seconds: int
    voice: str
    crop_offset: float = 0.0
    retain_background_audio: bool = False
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFD54F"
    margin_bottom: int = 430
    schema_version: int = 1

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "source_url": self.source_url,
            "staging_path": self.staging_path,
            "background_path": self.background_path,
            "preset": self.preset,
            "target_duration_seconds": self.target_duration_seconds,
            "voice": self.voice,
            "crop_offset": self.crop_offset,
            "retain_background_audio": self.retain_background_audio,
            "primary_color": self.primary_color,
            "highlight_color": self.highlight_color,
            "margin_bottom": self.margin_bottom,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProjectSettings:
        if not isinstance(value, dict):
            raise ConfigurationError("Project settings must be an object.")
        data = cast(dict[object, object], value)
        version = data.get("schema_version")
        source_url = data.get("source_url")
        staging_path = data.get("staging_path")
        background_path = data.get("background_path")
        preset = data.get("preset")
        duration = data.get("target_duration_seconds")
        voice = data.get("voice")
        crop_offset = data.get("crop_offset", 0.0)
        retain_audio = data.get("retain_background_audio", False)
        primary = data.get("primary_color", "#FFFFFF")
        highlight = data.get("highlight_color", "#FFD54F")
        margin_bottom = data.get("margin_bottom", 430)
        if version != 1:
            raise ConfigurationError("Project settings schema is unsupported.")
        if not isinstance(source_url, str) or not source_url.strip():
            raise ConfigurationError("Project Reddit URL is invalid.")
        if not isinstance(staging_path, str) or not staging_path.strip():
            raise ConfigurationError("Project staging path is invalid.")
        if not isinstance(background_path, str) or not background_path.strip():
            raise ConfigurationError("Project background path is invalid.")
        if preset not in {"subway", "minecraft"}:
            raise ConfigurationError("Project background preset is invalid.")
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or not 15 <= duration <= 3600
        ):
            raise ConfigurationError("Project duration must be from 15 to 3600 seconds.")
        if not isinstance(voice, str) or not voice.strip():
            raise ConfigurationError("Project voice is invalid.")
        if (
            not isinstance(crop_offset, int | float)
            or isinstance(crop_offset, bool)
            or not -1 <= float(crop_offset) <= 1
        ):
            raise ConfigurationError("Project crop offset must be between -1 and 1.")
        if not isinstance(retain_audio, bool):
            raise ConfigurationError("Project gameplay audio setting is invalid.")
        primary = _color(primary, "primary")
        highlight = _color(highlight, "highlight")
        if (
            not isinstance(margin_bottom, int)
            or isinstance(margin_bottom, bool)
            or not 0 <= margin_bottom <= 1500
        ):
            raise ConfigurationError("Project caption position is invalid.")
        return cls(
            schema_version=1,
            source_url=source_url.strip(),
            staging_path=staging_path.strip(),
            background_path=background_path,
            preset=cast(Literal["subway", "minecraft"], preset),
            target_duration_seconds=duration,
            voice=voice.strip(),
            crop_offset=float(crop_offset),
            retain_background_audio=retain_audio,
            primary_color=primary,
            highlight_color=highlight.upper(),
            margin_bottom=margin_bottom,
        )

    def apply(self, base: AppConfig) -> AppConfig:
        return replace(
            base,
            selection=replace(
                base.selection,
                target_duration_seconds=self.target_duration_seconds,
            ),
            speech=replace(base.speech, voice=self.voice),
            media=replace(
                base.media,
                retain_background_audio=self.retain_background_audio,
            ),
            captions=replace(
                base.captions,
                primary_color=self.primary_color,
                highlight_color=self.highlight_color,
                margin_bottom=self.margin_bottom,
            ),
        )


def _color(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        raise ConfigurationError(f"Project {label} color is invalid.")
    try:
        int(value[1:], 16)
    except ValueError as exc:
        raise ConfigurationError(f"Project {label} color is invalid.") from exc
    return value.upper()
