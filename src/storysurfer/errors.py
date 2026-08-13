"""Structured application errors."""

from __future__ import annotations


class StorySurferError(Exception):
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


class ConfigurationError(StorySurferError):
    """Configuration is absent or invalid."""

    exit_code = 2


class SourceError(StorySurferError):
    """A source could not be parsed or retrieved."""

    exit_code = 3


class StorageError(StorySurferError):
    """A run or artifact could not be stored safely."""

    exit_code = 4


class CapabilityError(StorySurferError):
    """A required local executable or feature is unavailable."""

    exit_code = 5


class SelectionError(StorySurferError):
    """A thread cannot produce a usable editorial selection."""

    exit_code = 6


class ScriptError(StorySurferError):
    """A source-linked narration script cannot be created or read."""

    exit_code = 7


class SpeechError(StorySurferError):
    """Speech synthesis or audio composition failed."""

    exit_code = 8


class AlignmentError(SpeechError):
    """Speech timing data is absent, invalid, or incompatible with the audio."""


class CaptionError(StorySurferError):
    """Word timings cannot produce valid caption artifacts."""

    exit_code = 9


class MediaError(StorySurferError):
    """Input media is missing, corrupt, unsupported, or cannot be rendered."""

    exit_code = 10


class RightsError(StorySurferError):
    """A final render was requested without the required rights acknowledgement."""

    exit_code = 11


class VerificationError(StorySurferError):
    """A rendered artifact failed an objective quality check."""

    exit_code = 12


class JobCancelled(StorySurferError):
    """A durable local job was cooperatively cancelled."""

    exit_code = 13


class WebError(StorySurferError):
    """A browser request or local web operation is invalid."""

    exit_code = 14
