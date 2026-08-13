"""Structured application errors."""

from __future__ import annotations


class RedditSurferError(Exception):
    """Base class for expected, user-facing failures."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def display(self) -> str:
        if self.hint:
            return f"{self.message}\nHint: {self.hint}"
        return self.message


class ConfigurationError(RedditSurferError):
    """Configuration is absent or invalid."""

    exit_code = 2


class SourceError(RedditSurferError):
    """A source could not be parsed or retrieved."""

    exit_code = 3


class StorageError(RedditSurferError):
    """A run or artifact could not be stored safely."""

    exit_code = 4


class CapabilityError(RedditSurferError):
    """A required local executable or feature is unavailable."""

    exit_code = 5


class SelectionError(RedditSurferError):
    """A thread cannot produce a usable editorial selection."""

    exit_code = 6


class ScriptError(RedditSurferError):
    """A source-linked narration script cannot be created or read."""

    exit_code = 7


class SpeechError(RedditSurferError):
    """Speech synthesis or audio composition failed."""

    exit_code = 8


class AlignmentError(SpeechError):
    """Speech timing data is absent, invalid, or incompatible with the audio."""
