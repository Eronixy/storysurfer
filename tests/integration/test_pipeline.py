from __future__ import annotations

from dataclasses import dataclass

from redditsurfer.config import AppConfig
from redditsurfer.pipeline import ingest, select
from redditsurfer.reddit.models import RawComment, RawPost, RawThread
from redditsurfer.reddit.url import RedditReference
from redditsurfer.storage import RunStorage


@dataclass
class FakeSource:
    raw: RawThread

    def fetch(self, reference: RedditReference) -> RawThread:
        assert reference.submission_id == "abc123"
        return self.raw


def test_ingest_and_select_write_resumable_artifacts(app_config: AppConfig) -> None:
    raw = RawThread(
        source_url="https://www.reddit.com/comments/abc123/",
        post=RawPost(
            id="abc123",
            title="A synthetic test story",
            body="I needed advice about a project deadline and explained the complete situation.",
            author_name="fixture-op",
            score=100,
            permalink="/r/synthetic/comments/abc123/story/",
            over_18=False,
            locked=False,
            removed_by_category=None,
            quarantined=False,
        ),
        comments=(
            RawComment(
                id="comment1",
                parent_id="t3_abc123",
                author_name="fixture-commenter",
                body="Did you tell the project team why the deadline needed to change?",
                score=50,
                depth=0,
                order=0,
                permalink="/r/synthetic/comments/abc123/story/comment1/",
                created_utc=1.0,
                is_submitter=False,
                removed_by_category=None,
            ),
            RawComment(
                id="opreply1",
                parent_id="t1_comment1",
                author_name="fixture-op",
                body="Yes, and the team agreed to a smaller scope that could ship safely.",
                score=40,
                depth=1,
                order=1,
                permalink="/r/synthetic/comments/abc123/story/opreply1/",
                created_utc=2.0,
                is_submitter=True,
                removed_by_category=None,
            ),
        ),
    )
    storage = RunStorage(app_config.storage.runs_dir)
    run_id, snapshot = ingest(
        "abc123",
        app_config,
        storage,
        source_factory=lambda _: FakeSource(raw),
        run_id="integration-run",
    )
    result = select(run_id, app_config, storage)

    assert len(snapshot.comments) == 2
    assert storage.artifact_path(run_id, "thread.json").is_file()
    assert storage.artifact_path(run_id, "selection.json").is_file()
    assert any(candidate.kind == "op_exchange" for candidate in result.candidates)
    manifest = storage.read_json(run_id, "manifest.json")
    assert isinstance(manifest, dict)
    assert manifest["stages"]["ingest"]["status"] == "completed"
    assert manifest["stages"]["select"]["status"] == "completed"
