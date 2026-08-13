"""Grounded, deterministic cleanup for spoken Reddit text."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from storysurfer.editorial.text import word_count

MARKDOWN_LINK = re.compile(r"\[([^\]]+)]\((?:https?://|www\.)[^)]+\)", re.IGNORECASE)
URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)")
REDDIT_USERNAME = re.compile(r"(?<!\w)(?:u/|/u/)[A-Za-z0-9_-]{3,20}\b", re.IGNORECASE)
MARKDOWN_TOKEN = re.compile(r"(?:^|\s)(?:#{1,6}|>|[-*+]\s)|[*_~`]+", re.MULTILINE)
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
    text = MARKDOWN_TOKEN.sub(" ", text)
    for source, spoken in pronunciations:
        text = re.sub(rf"\b{re.escape(source)}\b", spoken, text, flags=re.IGNORECASE)
    return CleanedText(WHITESPACE.sub(" ", text).strip(), tuple(sorted(set(redactions))))


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
