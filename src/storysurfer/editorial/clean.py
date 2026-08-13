"""Grounded, deterministic cleanup for spoken Reddit text."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from storysurfer.editorial.text import word_count

SPEECH_CLEANER_VERSION = 4

MARKDOWN_ESCAPE = re.compile(r"\\([\\`*{}\[\]()#+\-.!_>~|])")
MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)]\((?:https?://|www\.)[^)]+\)", re.IGNORECASE)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)]\((?:https?://|www\.)[^)]+\)", re.IGNORECASE)
URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)")
REDDIT_USERNAME = re.compile(r"(?<!\w)(?:u/|/u/)[A-Za-z0-9_-]{3,20}\b", re.IGNORECASE)
MARKDOWN_BLOCK_PREFIX = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>{1,3}\s*|[-+*]\s+)", re.MULTILINE)
MARKDOWN_SPOILER = re.compile(r"(?:>!|!<|\|\|)")
MARKDOWN_TOKEN = re.compile(r"[*_~`]+")
SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")
WHITESPACE = re.compile(r"\s+")
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class CleanedText:
    text: str
    redactions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractedText:
    text: str
    shortened: bool


def clean_for_speech(
    value: str,
    *,
    pronunciations: tuple[tuple[str, str], ...] = (),
) -> CleanedText:
    """Remove markup/identifiers without inventing source content."""
    text = html.unescape(value)
    text = _unescape_markdown(text)
    text = MARKDOWN_IMAGE.sub(r"\1", text)
    text = MARKDOWN_LINK.sub(r"\1", text)
    redactions: list[str] = []
    text, count = EMAIL.subn("redacted email address", text)
    if count:
        redactions.append("email")
    text, count = PHONE.subn("redacted phone number", text)
    if count:
        redactions.append("phone")
    text, count = REDDIT_USERNAME.subn("a Reddit user", text)
    if count:
        redactions.append("username")
    text, count = URL.subn("link omitted", text)
    if count:
        redactions.append("url")
    text = MARKDOWN_SPOILER.sub("", text)
    text = MARKDOWN_BLOCK_PREFIX.sub("", text)
    text = MARKDOWN_TOKEN.sub(" ", text)
    text = text.replace("\\", "")
    text = _strip_emoji(text)
    for source, spoken in pronunciations:
        text = re.sub(rf"\b{re.escape(source)}\b", spoken, text, flags=re.IGNORECASE)
    text = SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
    return CleanedText(WHITESPACE.sub(" ", text).strip(), tuple(sorted(set(redactions))))


def _unescape_markdown(value: str) -> str:
    """Unwrap repeated Reddit escaping around Markdown punctuation."""
    while True:
        unescaped = MARKDOWN_ESCAPE.sub(r"\1", value)
        if unescaped == value:
            return value
        value = unescaped


def _strip_emoji(value: str) -> str:
    """Replace emoji code points and their joiners with spaces for stable TTS."""
    result: list[str] = []
    for character in value:
        codepoint = ord(character)
        is_emoji = (
            0x1F000 <= codepoint <= 0x1FAFF
            or 0x2600 <= codepoint <= 0x26FF
            or 0x2700 <= codepoint <= 0x27BF
            or 0x2300 <= codepoint <= 0x23FF
            or 0x2B00 <= codepoint <= 0x2BFF
            or 0xFE00 <= codepoint <= 0xFE0F
            or 0x1F1E6 <= codepoint <= 0x1F1FF
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or codepoint in {0x200D, 0x20E3}
        )
        result.append(" " if is_emoji else character)
    return "".join(result)


def sentence_extract(value: str, max_words: int) -> ExtractedText:
    """Fit complete sentences into a word budget; never cut a sentence mid-way."""
    normalized = WHITESPACE.sub(" ", value).strip()
    if not normalized or word_count(normalized) <= max_words:
        return ExtractedText(normalized, False)
    sentences = [item.strip() for item in SENTENCE_BOUNDARY.split(normalized) if item.strip()]
    selected: list[str] = []
    used = 0
    for sentence in sentences:
        sentence_words = word_count(sentence)
        if selected and used + sentence_words > max_words:
            break
        if not selected and sentence_words > max_words:
            selected.append(sentence)
            break
        selected.append(sentence)
        used += sentence_words
    extracted = " ".join(selected)
    return ExtractedText(extracted, extracted != normalized)
