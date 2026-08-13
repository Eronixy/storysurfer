"""Small deterministic text helpers used by Phase 1 selection."""

from __future__ import annotations

import html
import re

WORD_PATTERN = re.compile(r"[\w']+", re.UNICODE)
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
MARKDOWN_PATTERN = re.compile(r"[*_~`>#\[\]()]")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "his",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "she",
    "so",
    "that",
    "the",
    "their",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "with",
    "you",
    "your",
}


def plain_text(value: str) -> str:
    decoded = html.unescape(value)
    without_urls = URL_PATTERN.sub(" ", decoded)
    without_markup = MARKDOWN_PATTERN.sub(" ", without_urls)
    return " ".join(without_markup.split())


def words(value: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in WORD_PATTERN.finditer(plain_text(value)))


def content_words(value: str) -> frozenset[str]:
    return frozenset(word for word in words(value) if len(word) > 2 and word not in STOP_WORDS)


def word_count(value: str) -> int:
    return len(words(value))


def similarity(left: str, right: str) -> float:
    left_words = content_words(left)
    right_words = content_words(right)
    union = left_words | right_words
    if not union:
        return 0.0
    return len(left_words & right_words) / len(union)
