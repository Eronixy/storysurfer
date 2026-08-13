"""Edge TTS voice catalog normalization for browser controls."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import aiohttp
import edge_tts
from edge_tts.exceptions import EdgeTTSException

from storysurfer.errors import SpeechError

VoiceRecord = Mapping[str, object]
VoiceFetcher = Callable[[], Awaitable[Sequence[VoiceRecord]]]


@dataclass(frozen=True, slots=True)
class EdgeVoice:
    """Provider-neutral fields needed to present and filter one Edge voice."""

    name: str
    locale: str
    language: str
    gender: str
    categories: tuple[str, ...] = ()
    personalities: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        display_name = self.name.removeprefix(f"{self.locale}-").removesuffix("Neural")
        display_name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", display_name)
        specialties = self.categories or self.personalities
        detail = f" · {', '.join(specialties)}" if specialties else ""
        return f"{display_name} — {self.gender}{detail}"


async def fetch_edge_voices(*, fetcher: VoiceFetcher | None = None) -> tuple[EdgeVoice, ...]:
    """Fetch and normalize the current Edge catalog without leaking SDK records."""
    try:
        records = await (fetcher or _fetch_live_records)()
    except (EdgeTTSException, aiohttp.ClientError, TimeoutError) as exc:
        raise SpeechError(
            "The Edge TTS voice catalog is temporarily unavailable.",
            hint="Check network access and reload; the configured default voice remains usable.",
        ) from exc
    return normalize_edge_voices(records)


def normalize_edge_voices(records: Sequence[VoiceRecord]) -> tuple[EdgeVoice, ...]:
    """Validate provider records and return stable, deduplicated dropdown data."""
    voices: dict[str, EdgeVoice] = {}
    for record in records:
        name = record.get("ShortName")
        locale = record.get("Locale")
        gender = record.get("Gender")
        friendly_name = record.get("FriendlyName")
        if not all(isinstance(item, str) and item.strip() for item in (name, locale, gender)):
            continue
        assert isinstance(name, str)
        assert isinstance(locale, str)
        assert isinstance(gender, str)
        language = _language_label(friendly_name, locale)
        tag = record.get("VoiceTag")
        categories: tuple[str, ...] = ()
        personalities: tuple[str, ...] = ()
        if isinstance(tag, dict):
            categories = _string_tuple(tag.get("ContentCategories"))
            personalities = _string_tuple(tag.get("VoicePersonalities"))
        voices[name] = EdgeVoice(
            name=name,
            locale=locale,
            language=language,
            gender=gender,
            categories=categories,
            personalities=personalities,
        )
    if not voices:
        raise SpeechError("Edge TTS returned no usable voices.")
    return tuple(sorted(voices.values(), key=lambda item: (item.language, item.name)))


def language_choices(voices: Sequence[EdgeVoice]) -> list[tuple[str, str]]:
    """Return unique language/locale filters in display order."""
    labels = {voice.locale: f"{voice.language} — {voice.locale}" for voice in voices}
    return sorted(((label, locale) for locale, label in labels.items()), key=lambda item: item[0])


def voice_choices(
    voices: Sequence[EdgeVoice], locale: str, *, selected: str | None = None
) -> tuple[list[tuple[str, str]], str | None]:
    """Return voices specialized for a locale and a valid selected value."""
    matching = [voice for voice in voices if voice.locale == locale]
    choices = [(voice.label, voice.name) for voice in matching]
    names = {voice.name for voice in matching}
    value = selected if selected in names else (matching[0].name if matching else None)
    return choices, value


def locale_for_voice(voices: Sequence[EdgeVoice], name: str) -> str:
    """Resolve a configured voice to its catalog locale with a name-based fallback."""
    for voice in voices:
        if voice.name == name:
            return voice.locale
    parts = name.split("-", 2)
    return "-".join(parts[:2]) if len(parts) >= 2 else "en-US"


async def _fetch_live_records() -> Sequence[VoiceRecord]:
    return cast(Sequence[VoiceRecord], await edge_tts.list_voices())


def _language_label(friendly_name: object, locale: str) -> str:
    if isinstance(friendly_name, str) and " - " in friendly_name:
        label = friendly_name.rsplit(" - ", 1)[-1].strip()
        if label:
            return label
    return locale


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
