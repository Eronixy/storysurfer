"""Raw values copied from the Reddit provider before normalization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RawPost:
    id: str
    title: str
    body: str
    author_name: str | None
    score: int
    permalink: str
    over_18: bool
    locked: bool
    removed_by_category: str | None
    quarantined: bool


@dataclass(frozen=True, slots=True)
class RawComment:
    id: str
    parent_id: str
    author_name: str | None
    body: str
    score: int
    depth: int
    order: int
    permalink: str
    created_utc: float | None
    is_submitter: bool
    removed_by_category: str | None


@dataclass(frozen=True, slots=True)
class RawThread:
    source_url: str
    post: RawPost
    comments: tuple[RawComment, ...]
