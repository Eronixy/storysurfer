from __future__ import annotations

from dataclasses import dataclass, replace

from redditsurfer.config import AppConfig
from redditsurfer.domain import JsonValue
from redditsurfer.pipeline import caption_run, ingest, narrate, script_run, select
from redditsurfer.reddit.models import RawComment, RawPost, RawThread
from redditsurfer.reddit.url import RedditReference
from redditsurfer.speech.base import RelativeWord, SegmentSpeech
from redditsurfer.storage import RunStorage


@dataclass
class FakeSource:
    raw: RawThread

    def fetch(self, reference: RedditReference) -> RawThread:
        assert reference.submission_id == "abc123"
        return self.raw


@dataclass
class FakeSpeech:
    @property
    def provider_id(self) -> str:
        return "fake"

    def settings(self) -> dict[str, JsonValue]:
        return {"provider": "fake", "sample_rate": 24_000}

    def synthesize(self, text: str) -> SegmentSpeech:
        tokens = text.split()
        pcm = b"\x00\x00" * (len(tokens) * 2_400)
        words = tuple(
            RelativeWord(token, index * 100, (index + 1) * 100)
            for index, token in enumerate(tokens)
        )
        return SegmentSpeech(pcm_s16le=pcm, sample_rate=24_000, words=words)


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
    script = script_run(run_id, app_config, storage)
    speech = narrate(
        run_id,
        app_config,
        storage,
        speech_factory=lambda _: FakeSpeech(),
    )
    captions = caption_run(run_id, app_config, storage)

    assert len(snapshot.comments) == 2
    assert storage.artifact_path(run_id, "thread.json").is_file()
    assert storage.artifact_path(run_id, "selection.json").is_file()
    assert any(candidate.kind == "op_exchange" for candidate in result.candidates)
    assert all(segment.source_refs for segment in script.segments)
    assert speech.words
    assert storage.internal_path(run_id, speech.audio_path).is_file()
    assert captions.cues
    assert storage.artifact_path(run_id, "captions.ass").is_file()
    assert storage.artifact_path(run_id, "captions.srt").is_file()
    manifest = storage.read_json(run_id, "manifest.json")
    assert isinstance(manifest, dict)
    assert manifest["stages"]["ingest"]["status"] == "completed"
    assert manifest["stages"]["select"]["status"] == "completed"
    assert manifest["stages"]["script"]["status"] == "completed"
    assert manifest["stages"]["synthesize"]["status"] == "completed"
    assert manifest["stages"]["caption"]["status"] == "completed"

    changed_config = replace(
        app_config,
        speech=replace(
            app_config.speech,
            pronunciations=(("project", "initiative"),),
        ),
    )
    script_run(run_id, changed_config, storage)
    stale_manifest = storage.read_json(run_id, "manifest.json")
    assert isinstance(stale_manifest, dict)
    assert stale_manifest["stages"]["synthesize"]["status"] == "stale"
