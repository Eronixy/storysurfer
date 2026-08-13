from __future__ import annotations

import pytest

from storysurfer.errors import SourceError
from storysurfer.reddit.url import parse_reddit_reference


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc123", "abc123"),
        ("https://www.reddit.com/r/test/comments/ABC123/title/", "abc123"),
        ("https://redd.it/abc123?share_id=ignored", "abc123"),
        ("https://old.reddit.com/comments/abc123/", "abc123"),
    ],
)
def test_parse_supported_references(value: str, expected: str) -> None:
    reference = parse_reddit_reference(value)

    assert reference.submission_id == expected
    assert reference.canonical_url == f"https://www.reddit.com/comments/{expected}/"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/comments/abc123/",
        "https://www.reddit.com/r/test/",
        "not an id",
    ],
)
def test_reject_invalid_references(value: str) -> None:
    with pytest.raises(SourceError):
        parse_reddit_reference(value)
