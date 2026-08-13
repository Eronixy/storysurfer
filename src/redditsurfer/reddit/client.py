"""Authenticated PRAW adapter for public Reddit submissions."""

from __future__ import annotations

from typing import Protocol

import praw
from praw.exceptions import PRAWException
from praw.models import Comment as PrawComment
from prawcore.exceptions import PrawcoreException

from redditsurfer.config import RedditConfig
from redditsurfer.errors import ConfigurationError, SourceError
from redditsurfer.reddit.models import RawComment, RawPost, RawThread
from redditsurfer.reddit.url import RedditReference, parse_reddit_reference


class RedditThreadSource(Protocol):
    def fetch(self, reference: RedditReference) -> RawThread: ...


class PrawRedditClient:
    """Read-only Reddit source that copies provider objects into raw dataclasses."""

    def __init__(self, config: RedditConfig) -> None:
        if not config.credentials_configured:
            raise ConfigurationError(
                "Reddit credentials are not configured.",
                hint="Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in the environment.",
            )
        self._config = config

    def fetch(self, reference: RedditReference) -> RawThread:
        try:
            reddit = praw.Reddit(
                client_id=self._config.client_id,
                client_secret=self._config.client_secret,
                user_agent=self._config.user_agent,
                check_for_async=False,
                check_for_updates=False,
            )
            submission = reddit.submission(id=reference.submission_id)
            submission.comment_sort = self._config.comment_sort
            post = RawPost(
                id=str(submission.id),
                title=str(submission.title),
                body=str(submission.selftext or ""),
                author_name=_author_name(submission.author),
                score=int(submission.score),
                permalink=str(submission.permalink),
                over_18=bool(submission.over_18),
                locked=bool(submission.locked),
                removed_by_category=_optional_text(
                    getattr(submission, "removed_by_category", None)
                ),
                quarantined=bool(getattr(submission, "quarantine", False)),
            )
            submission.comments.replace_more(limit=self._config.replace_more_limit)
            raw_comments: list[RawComment] = []
            for order, comment in enumerate(
                submission.comments.list()[: self._config.max_comments]
            ):
                if not isinstance(comment, PrawComment):
                    continue
                raw_comments.append(
                    RawComment(
                        id=str(comment.id),
                        parent_id=str(comment.parent_id),
                        author_name=_author_name(comment.author),
                        body=str(comment.body or ""),
                        score=int(comment.score),
                        depth=int(comment.depth),
                        order=order,
                        permalink=str(comment.permalink),
                        created_utc=_optional_float(getattr(comment, "created_utc", None)),
                        is_submitter=bool(comment.is_submitter),
                        removed_by_category=_optional_text(
                            getattr(comment, "removed_by_category", None)
                        ),
                    )
                )
        except (PRAWException, PrawcoreException) as exc:
            raise SourceError(
                f"Reddit could not retrieve submission {reference.submission_id}.",
                hint="Check the URL, credentials, API access, and whether the post is public.",
            ) from exc
        return RawThread(
            source_url=reference.canonical_url,
            post=post,
            comments=tuple(raw_comments),
        )


def fetch_reference(source: RedditThreadSource, value: str) -> RawThread:
    return source.fetch(parse_reddit_reference(value))


def _author_name(author: object) -> str | None:
    if author is None:
        return None
    name = getattr(author, "name", None)
    return str(name) if name else None


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return float(value)
