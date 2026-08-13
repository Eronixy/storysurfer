"""Strict parsing for Reddit submission URLs and IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from redditsurfer.errors import SourceError

SUBMISSION_ID = re.compile(r"^[a-z0-9]{5,10}$", re.IGNORECASE)
ALLOWED_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "np.reddit.com",
    "redd.it",
    "www.redd.it",
}


@dataclass(frozen=True, slots=True)
class RedditReference:
    submission_id: str
    canonical_url: str


def parse_reddit_reference(value: str) -> RedditReference:
    candidate = value.strip()
    if SUBMISSION_ID.fullmatch(candidate):
        normalized_candidate = candidate.lower()
        return RedditReference(normalized_candidate, _canonical_url(normalized_candidate))

    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise SourceError(
            "Expected a Reddit submission URL or ID.",
            hint="Use a reddit.com /comments/... URL, a redd.it short URL, or a submission ID.",
        )
    parts = [part for part in parsed.path.split("/") if part]
    submission_id: str | None = None
    if host.endswith("redd.it") and parts:
        submission_id = parts[0]
    elif "comments" in parts:
        index = parts.index("comments")
        if index + 1 < len(parts):
            submission_id = parts[index + 1]
    if submission_id is None or not SUBMISSION_ID.fullmatch(submission_id):
        raise SourceError("Reddit URL does not contain a valid submission ID.")
    normalized_id = submission_id.lower()
    return RedditReference(normalized_id, _canonical_url(normalized_id))


def _canonical_url(submission_id: str) -> str:
    return f"https://www.reddit.com/comments/{submission_id}/"
