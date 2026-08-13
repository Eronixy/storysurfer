from __future__ import annotations

import asyncio

from storysurfer.speech.voices import (
    fetch_edge_voices,
    language_choices,
    locale_for_voice,
    voice_choices,
)


def test_edge_voice_catalog_filters_by_specialized_locale() -> None:
    async def records() -> list[dict[str, object]]:
        return [
            {
                "ShortName": "en-US-EmmaNeural",
                "Locale": "en-US",
                "Gender": "Female",
                "FriendlyName": "Microsoft Emma - English (United States)",
                "VoiceTag": {
                    "ContentCategories": ["Conversation", "Copilot"],
                    "VoicePersonalities": ["Cheerful"],
                },
            },
            {
                "ShortName": "fil-PH-AngeloNeural",
                "Locale": "fil-PH",
                "Gender": "Male",
                "FriendlyName": "Microsoft Angelo - Filipino (Philippines)",
                "VoiceTag": {
                    "ContentCategories": ["General"],
                    "VoicePersonalities": ["Friendly", "Positive"],
                },
            },
        ]

    voices = asyncio.run(fetch_edge_voices(fetcher=records))

    assert language_choices(voices) == [
        ("English (United States) — en-US", "en-US"),
        ("Filipino (Philippines) — fil-PH", "fil-PH"),
    ]
    choices, selected = voice_choices(voices, "fil-PH")
    assert choices == [("Angelo — Male · General", "fil-PH-AngeloNeural")]
    assert selected == "fil-PH-AngeloNeural"
    assert locale_for_voice(voices, "en-US-EmmaNeural") == "en-US"


def test_edge_voice_catalog_skips_invalid_and_deduplicates_names() -> None:
    async def records() -> list[dict[str, object]]:
        return [
            {"ShortName": "missing-fields"},
            {
                "ShortName": "en-GB-RyanNeural",
                "Locale": "en-GB",
                "Gender": "Male",
                "FriendlyName": "Microsoft Ryan - English (United Kingdom)",
            },
            {
                "ShortName": "en-GB-RyanNeural",
                "Locale": "en-GB",
                "Gender": "Male",
                "FriendlyName": "Microsoft Ryan - English (United Kingdom)",
            },
        ]

    voices = asyncio.run(fetch_edge_voices(fetcher=records))

    assert [voice.name for voice in voices] == ["en-GB-RyanNeural"]
