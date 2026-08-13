"""Provider-neutral domain models and versioned JSON representations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

THREAD_SCHEMA_VERSION = 1
SELECTION_SCHEMA_VERSION = 1
SCRIPT_SCHEMA_VERSION = 1
SPEECH_SCHEMA_VERSION = 1


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _number(value: object, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number")
    return float(value)


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be an object")
    return cast(dict[str, object], value)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class Post:
    id: str
    title: str
    body: str
    author_id: str | None
    score: int
    permalink: str
    nsfw: bool = False
    locked: bool = False
    removed: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "author_id": self.author_id,
            "score": self.score,
            "permalink": self.permalink,
            "nsfw": self.nsfw,
            "locked": self.locked,
            "removed": self.removed,
        }

    @classmethod
    def from_dict(cls, value: object) -> Post:
        data = _mapping(value, "post")
        return cls(
            id=_string(data.get("id"), "post.id"),
            title=_string(data.get("title"), "post.title"),
            body=_string(data.get("body"), "post.body"),
            author_id=_optional_string(data.get("author_id"), "post.author_id"),
            score=_integer(data.get("score"), "post.score"),
            permalink=_string(data.get("permalink"), "post.permalink"),
            nsfw=_boolean(data.get("nsfw", False), "post.nsfw"),
            locked=_boolean(data.get("locked", False), "post.locked"),
            removed=_boolean(data.get("removed", False), "post.removed"),
        )


@dataclass(frozen=True, slots=True)
class Comment:
    id: str
    parent_id: str
    author_id: str | None
    body: str
    score: int
    depth: int
    order: int
    permalink: str
    created_utc: float | None = None
    is_op: bool = False
    is_bot: bool = False
    removed: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "author_id": self.author_id,
            "body": self.body,
            "score": self.score,
            "depth": self.depth,
            "order": self.order,
            "permalink": self.permalink,
            "created_utc": self.created_utc,
            "is_op": self.is_op,
            "is_bot": self.is_bot,
            "removed": self.removed,
        }

    @classmethod
    def from_dict(cls, value: object) -> Comment:
        data = _mapping(value, "comment")
        created = data.get("created_utc")
        if created is not None and not isinstance(created, int | float):
            raise ValueError("comment.created_utc must be a number or null")
        return cls(
            id=_string(data.get("id"), "comment.id"),
            parent_id=_string(data.get("parent_id"), "comment.parent_id"),
            author_id=_optional_string(data.get("author_id"), "comment.author_id"),
            body=_string(data.get("body"), "comment.body"),
            score=_integer(data.get("score"), "comment.score"),
            depth=_integer(data.get("depth"), "comment.depth"),
            order=_integer(data.get("order"), "comment.order"),
            permalink=_string(data.get("permalink"), "comment.permalink"),
            created_utc=float(created) if created is not None else None,
            is_op=_boolean(data.get("is_op", False), "comment.is_op"),
            is_bot=_boolean(data.get("is_bot", False), "comment.is_bot"),
            removed=_boolean(data.get("removed", False), "comment.removed"),
        )


@dataclass(frozen=True, slots=True)
class ThreadSnapshot:
    submission: Post
    comments: tuple[Comment, ...]
    retrieved_at: str
    source_url: str
    schema_version: int = THREAD_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "retrieved_at": self.retrieved_at,
            "source_url": self.source_url,
            "submission": self.submission.to_dict(),
            "comments": [comment.to_dict() for comment in self.comments],
        }

    @classmethod
    def from_dict(cls, value: object) -> ThreadSnapshot:
        data = _mapping(value, "thread")
        version = _integer(data.get("schema_version"), "thread.schema_version")
        if version != THREAD_SCHEMA_VERSION:
            raise ValueError(f"unsupported thread schema version: {version}")
        raw_comments = data.get("comments")
        if not isinstance(raw_comments, list):
            raise ValueError("thread.comments must be an array")
        return cls(
            schema_version=version,
            retrieved_at=_string(data.get("retrieved_at"), "thread.retrieved_at"),
            source_url=_string(data.get("source_url"), "thread.source_url"),
            submission=Post.from_dict(data.get("submission")),
            comments=tuple(Comment.from_dict(comment) for comment in raw_comments),
        )


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_type: Literal["post", "comment"]
    source_id: str
    permalink: str
    author_role: Literal["op", "commenter", "unknown"]
    original_text_hash: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "permalink": self.permalink,
            "author_role": self.author_role,
            "original_text_hash": self.original_text_hash,
        }

    @classmethod
    def from_dict(cls, value: object) -> SourceRef:
        data = _mapping(value, "source_ref")
        source_type = _string(data.get("source_type"), "source_ref.source_type")
        author_role = _string(data.get("author_role"), "source_ref.author_role")
        if source_type not in {"post", "comment"}:
            raise ValueError(f"unsupported source type: {source_type}")
        if author_role not in {"op", "commenter", "unknown"}:
            raise ValueError(f"unsupported author role: {author_role}")
        return cls(
            source_type=cast(Literal["post", "comment"], source_type),
            source_id=_string(data.get("source_id"), "source_ref.source_id"),
            permalink=_string(data.get("permalink"), "source_ref.permalink"),
            author_role=cast(Literal["op", "commenter", "unknown"], author_role),
            original_text_hash=_string(
                data.get("original_text_hash"), "source_ref.original_text_hash"
            ),
        )


@dataclass(frozen=True, slots=True)
class NarrationSegment:
    id: str
    kind: Literal["title", "post", "comment", "op_reply", "op_update"]
    speaker_label: str
    spoken_text: str
    original_excerpt: str
    source_refs: tuple[SourceRef, ...]
    priority: float
    shortened: bool = False
    redactions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "kind": self.kind,
            "speaker_label": self.speaker_label,
            "spoken_text": self.spoken_text,
            "original_excerpt": self.original_excerpt,
            "source_refs": [source.to_dict() for source in self.source_refs],
            "priority": self.priority,
            "shortened": self.shortened,
            "redactions": list(self.redactions),
        }

    @classmethod
    def from_dict(cls, value: object) -> NarrationSegment:
        data = _mapping(value, "narration_segment")
        kind = _string(data.get("kind"), "narration_segment.kind")
        if kind not in {"title", "post", "comment", "op_reply", "op_update"}:
            raise ValueError(f"unsupported narration segment kind: {kind}")
        raw_refs = data.get("source_refs")
        if not isinstance(raw_refs, list):
            raise ValueError("narration_segment.source_refs must be an array")
        refs = tuple(SourceRef.from_dict(item) for item in raw_refs)
        if not refs:
            raise ValueError("narration_segment.source_refs cannot be empty")
        return cls(
            id=_string(data.get("id"), "narration_segment.id"),
            kind=cast(
                Literal["title", "post", "comment", "op_reply", "op_update"], kind
            ),
            speaker_label=_string(
                data.get("speaker_label"), "narration_segment.speaker_label"
            ),
            spoken_text=_string(data.get("spoken_text"), "narration_segment.spoken_text"),
            original_excerpt=_string(
                data.get("original_excerpt"), "narration_segment.original_excerpt"
            ),
            source_refs=refs,
            priority=_number(data.get("priority"), "narration_segment.priority"),
            shortened=_boolean(
                data.get("shortened", False), "narration_segment.shortened"
            ),
            redactions=_string_tuple(
                data.get("redactions", []), "narration_segment.redactions"
            ),
        )


@dataclass(frozen=True, slots=True)
class NarrationScript:
    thread_id: str
    created_at: str
    target_duration_seconds: int
    estimated_duration_ms: int
    estimated_words: int
    segments: tuple[NarrationSegment, ...]
    warnings: tuple[str, ...] = ()
    revision: int = 1
    schema_version: int = SCRIPT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "target_duration_seconds": self.target_duration_seconds,
            "estimated_duration_ms": self.estimated_duration_ms,
            "estimated_words": self.estimated_words,
            "revision": self.revision,
            "segments": [segment.to_dict() for segment in self.segments],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, value: object) -> NarrationScript:
        data = _mapping(value, "narration_script")
        version = _integer(data.get("schema_version"), "narration_script.schema_version")
        if version != SCRIPT_SCHEMA_VERSION:
            raise ValueError(f"unsupported narration script schema version: {version}")
        raw_segments = data.get("segments")
        if not isinstance(raw_segments, list):
            raise ValueError("narration_script.segments must be an array")
        segments = tuple(NarrationSegment.from_dict(item) for item in raw_segments)
        if not segments:
            raise ValueError("narration_script.segments cannot be empty")
        return cls(
            schema_version=version,
            thread_id=_string(data.get("thread_id"), "narration_script.thread_id"),
            created_at=_string(data.get("created_at"), "narration_script.created_at"),
            target_duration_seconds=_integer(
                data.get("target_duration_seconds"),
                "narration_script.target_duration_seconds",
            ),
            estimated_duration_ms=_integer(
                data.get("estimated_duration_ms"), "narration_script.estimated_duration_ms"
            ),
            estimated_words=_integer(
                data.get("estimated_words"), "narration_script.estimated_words"
            ),
            revision=_integer(data.get("revision", 1), "narration_script.revision"),
            segments=segments,
            warnings=_string_tuple(data.get("warnings", []), "narration_script.warnings"),
        )


@dataclass(frozen=True, slots=True)
class SpokenWord:
    text: str
    start_ms: int
    end_ms: int
    segment_id: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "segment_id": self.segment_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> SpokenWord:
        data = _mapping(value, "spoken_word")
        return cls(
            text=_string(data.get("text"), "spoken_word.text"),
            start_ms=_integer(data.get("start_ms"), "spoken_word.start_ms"),
            end_ms=_integer(data.get("end_ms"), "spoken_word.end_ms"),
            segment_id=_string(data.get("segment_id"), "spoken_word.segment_id"),
        )


@dataclass(frozen=True, slots=True)
class SpeechArtifact:
    provider_id: str
    audio_path: str
    duration_ms: int
    sample_rate: int
    words: tuple[SpokenWord, ...]
    segment_cache_keys: dict[str, str]
    schema_version: int = SPEECH_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "audio_path": self.audio_path,
            "duration_ms": self.duration_ms,
            "sample_rate": self.sample_rate,
            "words": [word.to_dict() for word in self.words],
            "segment_cache_keys": dict(self.segment_cache_keys),
        }

    @classmethod
    def from_dict(cls, value: object) -> SpeechArtifact:
        data = _mapping(value, "speech_artifact")
        version = _integer(data.get("schema_version"), "speech_artifact.schema_version")
        if version != SPEECH_SCHEMA_VERSION:
            raise ValueError(f"unsupported speech schema version: {version}")
        raw_words = data.get("words")
        if not isinstance(raw_words, list):
            raise ValueError("speech_artifact.words must be an array")
        raw_keys = _mapping(data.get("segment_cache_keys"), "speech_artifact.segment_cache_keys")
        return cls(
            schema_version=version,
            provider_id=_string(data.get("provider_id"), "speech_artifact.provider_id"),
            audio_path=_string(data.get("audio_path"), "speech_artifact.audio_path"),
            duration_ms=_integer(data.get("duration_ms"), "speech_artifact.duration_ms"),
            sample_rate=_integer(data.get("sample_rate"), "speech_artifact.sample_rate"),
            words=tuple(SpokenWord.from_dict(item) for item in raw_words),
            segment_cache_keys={
                key: _string(item, f"speech_artifact.segment_cache_keys.{key}")
                for key, item in raw_keys.items()
            },
        )


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    id: str
    kind: Literal["comment", "op_exchange", "op_update"]
    source_ids: tuple[str, ...]
    score: float
    component_scores: dict[str, float]
    penalties: dict[str, float]
    reason_codes: tuple[str, ...]
    word_count: int
    selected: bool

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source_ids": list(self.source_ids),
            "score": self.score,
            "component_scores": dict(self.component_scores),
            "penalties": dict(self.penalties),
            "reason_codes": list(self.reason_codes),
            "word_count": self.word_count,
            "selected": self.selected,
        }

    @classmethod
    def from_dict(cls, value: object) -> SelectionCandidate:
        data = _mapping(value, "candidate")
        kind = _string(data.get("kind"), "candidate.kind")
        if kind not in {"comment", "op_exchange", "op_update"}:
            raise ValueError(f"unsupported candidate kind: {kind}")
        component_data = _mapping(data.get("component_scores"), "candidate.component_scores")
        penalty_data = _mapping(data.get("penalties"), "candidate.penalties")
        return cls(
            id=_string(data.get("id"), "candidate.id"),
            kind=cast(Literal["comment", "op_exchange", "op_update"], kind),
            source_ids=_string_tuple(data.get("source_ids"), "candidate.source_ids"),
            score=_number(data.get("score", 0.0), "candidate.score"),
            component_scores={
                key: _number(item, f"candidate.component_scores.{key}")
                for key, item in component_data.items()
            },
            penalties={
                key: _number(item, f"candidate.penalties.{key}")
                for key, item in penalty_data.items()
            },
            reason_codes=_string_tuple(data.get("reason_codes"), "candidate.reason_codes"),
            word_count=_integer(data.get("word_count"), "candidate.word_count"),
            selected=_boolean(data.get("selected"), "candidate.selected"),
        )


@dataclass(frozen=True, slots=True)
class SelectionResult:
    thread_id: str
    target_duration_seconds: int
    target_words: int
    post_word_budget: int
    comment_word_budget: int
    selected_comment_words: int
    candidates: tuple[SelectionCandidate, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: int = SELECTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "thread_id": self.thread_id,
            "target_duration_seconds": self.target_duration_seconds,
            "target_words": self.target_words,
            "post_word_budget": self.post_word_budget,
            "comment_word_budget": self.comment_word_budget,
            "selected_comment_words": self.selected_comment_words,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, value: object) -> SelectionResult:
        data = _mapping(value, "selection")
        version = _integer(data.get("schema_version"), "selection.schema_version")
        if version != SELECTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported selection schema version: {version}")
        raw_candidates = data.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("selection.candidates must be an array")
        return cls(
            schema_version=version,
            thread_id=_string(data.get("thread_id"), "selection.thread_id"),
            target_duration_seconds=_integer(
                data.get("target_duration_seconds"), "selection.target_duration_seconds"
            ),
            target_words=_integer(data.get("target_words"), "selection.target_words"),
            post_word_budget=_integer(
                data.get("post_word_budget"), "selection.post_word_budget"
            ),
            comment_word_budget=_integer(
                data.get("comment_word_budget"), "selection.comment_word_budget"
            ),
            selected_comment_words=_integer(
                data.get("selected_comment_words"), "selection.selected_comment_words"
            ),
            candidates=tuple(
                SelectionCandidate.from_dict(candidate) for candidate in raw_candidates
            ),
            warnings=_string_tuple(data.get("warnings", []), "selection.warnings"),
        )
