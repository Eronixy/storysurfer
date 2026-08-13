"""Provider-neutral domain models and versioned JSON representations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

THREAD_SCHEMA_VERSION = 1
SELECTION_SCHEMA_VERSION = 1


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
