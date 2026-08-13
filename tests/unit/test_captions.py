from __future__ import annotations

from redditsurfer.captions import build_caption_artifact, render_ass, render_srt
from redditsurfer.config import CaptionConfig, MediaConfig
from redditsurfer.domain import (
    NarrationScript,
    NarrationSegment,
    SourceRef,
    SpeechArtifact,
    SpokenWord,
)


def _script() -> NarrationScript:
    source = SourceRef("post", "post-1", "https://reddit.test/post-1", "op", "hash")
    return NarrationScript(
        thread_id="post-1",
        created_at="2026-01-01T00:00:00+00:00",
        target_duration_seconds=30,
        estimated_duration_ms=1_500,
        estimated_words=8,
        segments=(
            NarrationSegment(
                id="story",
                kind="post",
                speaker_label="OP",
                spoken_text="This starts the story. Then things changed",
                original_excerpt="This starts the story. Then things changed",
                source_refs=(source,),
                priority=1.0,
            ),
        ),
    )


def _speech() -> SpeechArtifact:
    tokens = ("This", "starts", "the", "story.", "Then", "things", "changed")
    starts = (0, 110, 220, 330, 800, 910, 1_020)
    words = tuple(
        SpokenWord(token, start, start + 90, "story")
        for token, start in zip(tokens, starts, strict=True)
    )
    return SpeechArtifact("fake", "speech/narration.wav", 1_200, 24_000, words, {})


def test_caption_chunks_follow_punctuation_and_measured_gaps() -> None:
    artifact = build_caption_artifact(_script(), _speech(), CaptionConfig())

    assert [cue.text for cue in artifact.cues] == [
        "This starts\nthe story.",
        "Then things\nchanged",
    ]
    assert [(cue.start_ms, cue.end_ms) for cue in artifact.cues] == [
        (0, 500),
        (800, 1_190),
    ]
    assert all(cue.end_ms <= artifact.duration_ms for cue in artifact.cues)
    assert all(len(cue.words) <= 5 for cue in artifact.cues)
    assert all(cue.text.count("\n") <= 1 for cue in artifact.cues)


def test_ass_and_srt_are_safe_and_word_timed() -> None:
    speech = _speech()
    injected = SpokenWord("{\\danger}", 1_020, 1_110, "story")
    speech = SpeechArtifact(
        speech.provider_id,
        speech.audio_path,
        speech.duration_ms,
        speech.sample_rate,
        (*speech.words[:-1], injected),
        speech.segment_cache_keys,
    )
    artifact = build_caption_artifact(_script(), speech, CaptionConfig())

    ass = render_ass(artifact, CaptionConfig(), MediaConfig())
    srt = render_srt(artifact)

    assert "PlayResX: 1080" in ass
    assert "\\t(0,120," in ass
    assert "{\\kf9}" in ass
    assert "{\\danger}" not in ass
    assert "｛＼danger｝" in ass
    assert "00:00:00,000 --> 00:00:00,500" in srt
