"""Provider-neutral domain models and versioned JSON representations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

THREAD_SCHEMA_VERSION = 1
SELECTION_SCHEMA_VERSION = 1
SCRIPT_SCHEMA_VERSION = 1
SPEECH_SCHEMA_VERSION = 1
CAPTION_SCHEMA_VERSION = 1
TIMELINE_SCHEMA_VERSION = 1
VERIFICATION_SCHEMA_VERSION = 1


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
class CaptionWord:
    text: str
    start_ms: int
    end_ms: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {"text": self.text, "start_ms": self.start_ms, "end_ms": self.end_ms}

    @classmethod
    def from_dict(cls, value: object) -> CaptionWord:
        data = _mapping(value, "caption_word")
        return cls(
            text=_string(data.get("text"), "caption_word.text"),
            start_ms=_integer(data.get("start_ms"), "caption_word.start_ms"),
            end_ms=_integer(data.get("end_ms"), "caption_word.end_ms"),
        )


@dataclass(frozen=True, slots=True)
class CaptionCue:
    id: str
    text: str
    start_ms: int
    end_ms: int
    segment_id: str
    speaker_label: str
    style: Literal["title", "story", "commenter", "op"]
    words: tuple[CaptionWord, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "segment_id": self.segment_id,
            "speaker_label": self.speaker_label,
            "style": self.style,
            "words": [word.to_dict() for word in self.words],
        }

    @classmethod
    def from_dict(cls, value: object) -> CaptionCue:
        data = _mapping(value, "caption_cue")
        style = _string(data.get("style"), "caption_cue.style")
        if style not in {"title", "story", "commenter", "op"}:
            raise ValueError(f"unsupported caption cue style: {style}")
        raw_words = data.get("words")
        if not isinstance(raw_words, list):
            raise ValueError("caption_cue.words must be an array")
        words = tuple(CaptionWord.from_dict(item) for item in raw_words)
        if not words:
            raise ValueError("caption_cue.words cannot be empty")
        return cls(
            id=_string(data.get("id"), "caption_cue.id"),
            text=_string(data.get("text"), "caption_cue.text"),
            start_ms=_integer(data.get("start_ms"), "caption_cue.start_ms"),
            end_ms=_integer(data.get("end_ms"), "caption_cue.end_ms"),
            segment_id=_string(data.get("segment_id"), "caption_cue.segment_id"),
            speaker_label=_string(data.get("speaker_label"), "caption_cue.speaker_label"),
            style=cast(Literal["title", "story", "commenter", "op"], style),
            words=words,
        )


@dataclass(frozen=True, slots=True)
class CaptionArtifact:
    duration_ms: int
    ass_path: str
    srt_path: str
    cues: tuple[CaptionCue, ...]
    schema_version: int = CAPTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "duration_ms": self.duration_ms,
            "ass_path": self.ass_path,
            "srt_path": self.srt_path,
            "cues": [cue.to_dict() for cue in self.cues],
        }

    @classmethod
    def from_dict(cls, value: object) -> CaptionArtifact:
        data = _mapping(value, "caption_artifact")
        version = _integer(data.get("schema_version"), "caption_artifact.schema_version")
        if version != CAPTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported caption schema version: {version}")
        raw_cues = data.get("cues")
        if not isinstance(raw_cues, list):
            raise ValueError("caption_artifact.cues must be an array")
        cues = tuple(CaptionCue.from_dict(item) for item in raw_cues)
        if not cues:
            raise ValueError("caption_artifact.cues cannot be empty")
        artifact = cls(
            schema_version=version,
            duration_ms=_integer(data.get("duration_ms"), "caption_artifact.duration_ms"),
            ass_path=_string(data.get("ass_path"), "caption_artifact.ass_path"),
            srt_path=_string(data.get("srt_path"), "caption_artifact.srt_path"),
            cues=cues,
        )
        if artifact.duration_ms <= 0:
            raise ValueError("caption_artifact.duration_ms must be positive")
        previous_cue_end = 0
        for cue in artifact.cues:
            if cue.start_ms < previous_cue_end or cue.end_ms <= cue.start_ms:
                raise ValueError("caption cues must be positive and monotonic")
            if cue.end_ms > artifact.duration_ms:
                raise ValueError("caption cue exceeds narration duration")
            previous_word_end = cue.start_ms
            for word in cue.words:
                if word.start_ms < previous_word_end or word.end_ms <= word.start_ms:
                    raise ValueError("caption words must be positive and monotonic")
                if word.start_ms < cue.start_ms or word.end_ms > cue.end_ms:
                    raise ValueError("caption word falls outside its cue")
                previous_word_end = word.end_ms
            previous_cue_end = cue.end_ms
        return artifact


@dataclass(frozen=True, slots=True)
class Timeline:
    duration_ms: int
    background_path: str
    background_duration_ms: int
    background_has_audio: bool
    background_looped: bool
    preset: Literal["subway", "minecraft"]
    crop_offset: float
    narration_path: str
    captions_path: str
    output_width: int
    output_height: int
    frame_rate: int
    retain_background_audio: bool
    profile: Literal["preview", "final"] = "preview"
    rights_acknowledged: bool = False
    schema_version: int = TIMELINE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "duration_ms": self.duration_ms,
            "background_path": self.background_path,
            "background_duration_ms": self.background_duration_ms,
            "background_has_audio": self.background_has_audio,
            "background_looped": self.background_looped,
            "preset": self.preset,
            "crop_offset": self.crop_offset,
            "narration_path": self.narration_path,
            "captions_path": self.captions_path,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "frame_rate": self.frame_rate,
            "retain_background_audio": self.retain_background_audio,
            "profile": self.profile,
            "rights_acknowledged": self.rights_acknowledged,
        }

    @classmethod
    def from_dict(cls, value: object) -> Timeline:
        data = _mapping(value, "timeline")
        version = _integer(data.get("schema_version"), "timeline.schema_version")
        if version != TIMELINE_SCHEMA_VERSION:
            raise ValueError(f"unsupported timeline schema version: {version}")
        preset = _string(data.get("preset"), "timeline.preset")
        if preset not in {"subway", "minecraft"}:
            raise ValueError(f"unsupported background preset: {preset}")
        profile = _string(data.get("profile", "preview"), "timeline.profile")
        if profile not in {"preview", "final"}:
            raise ValueError(f"unsupported render profile: {profile}")
        timeline = cls(
            schema_version=version,
            duration_ms=_integer(data.get("duration_ms"), "timeline.duration_ms"),
            background_path=_string(data.get("background_path"), "timeline.background_path"),
            background_duration_ms=_integer(
                data.get("background_duration_ms"), "timeline.background_duration_ms"
            ),
            background_has_audio=_boolean(
                data.get("background_has_audio"), "timeline.background_has_audio"
            ),
            background_looped=_boolean(
                data.get("background_looped"), "timeline.background_looped"
            ),
            preset=cast(Literal["subway", "minecraft"], preset),
            crop_offset=_number(data.get("crop_offset"), "timeline.crop_offset"),
            narration_path=_string(data.get("narration_path"), "timeline.narration_path"),
            captions_path=_string(data.get("captions_path"), "timeline.captions_path"),
            output_width=_integer(data.get("output_width"), "timeline.output_width"),
            output_height=_integer(data.get("output_height"), "timeline.output_height"),
            frame_rate=_integer(data.get("frame_rate"), "timeline.frame_rate"),
            retain_background_audio=_boolean(
                data.get("retain_background_audio"), "timeline.retain_background_audio"
            ),
            profile=cast(Literal["preview", "final"], profile),
            rights_acknowledged=_boolean(
                data.get("rights_acknowledged", False), "timeline.rights_acknowledged"
            ),
        )
        if timeline.duration_ms <= 0 or timeline.background_duration_ms <= 0:
            raise ValueError("timeline durations must be positive")
        if (
            timeline.output_width <= 0
            or timeline.output_height <= 0
            or timeline.frame_rate <= 0
        ):
            raise ValueError("timeline output settings must be positive")
        if not -1.0 <= timeline.crop_offset <= 1.0:
            raise ValueError("timeline.crop_offset must be between -1 and 1")
        return timeline


@dataclass(frozen=True, slots=True)
class QualityCheck:
    name: str
    passed: bool
    message: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"name": self.name, "passed": self.passed, "message": self.message}

    @classmethod
    def from_dict(cls, value: object) -> QualityCheck:
        data = _mapping(value, "quality_check")
        return cls(
            name=_string(data.get("name"), "quality_check.name"),
            passed=_boolean(data.get("passed"), "quality_check.passed"),
            message=_string(data.get("message"), "quality_check.message"),
        )


@dataclass(frozen=True, slots=True)
class VerificationReport:
    profile: Literal["preview", "final"]
    artifact_path: str
    checked_at: str
    passed: bool
    duration_ms: int
    width: int
    height: int
    frame_rate: float
    video_codec: str
    audio_codec: str | None
    pixel_format: str | None
    checks: tuple[QualityCheck, ...]
    schema_version: int = VERIFICATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "artifact_path": self.artifact_path,
            "checked_at": self.checked_at,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "pixel_format": self.pixel_format,
            "checks": [check.to_dict() for check in self.checks],
        }

    @classmethod
    def from_dict(cls, value: object) -> VerificationReport:
        data = _mapping(value, "verification_report")
        version = _integer(
            data.get("schema_version"), "verification_report.schema_version"
        )
        if version != VERIFICATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported verification schema version: {version}")
        profile = _string(data.get("profile"), "verification_report.profile")
        if profile not in {"preview", "final"}:
            raise ValueError(f"unsupported verification profile: {profile}")
        raw_checks = data.get("checks")
        if not isinstance(raw_checks, list):
            raise ValueError("verification_report.checks must be an array")
        checks = tuple(QualityCheck.from_dict(check) for check in raw_checks)
        passed = _boolean(data.get("passed"), "verification_report.passed")
        if passed != all(check.passed for check in checks):
            raise ValueError("verification_report.passed disagrees with its checks")
        return cls(
            schema_version=version,
            profile=cast(Literal["preview", "final"], profile),
            artifact_path=_string(
                data.get("artifact_path"), "verification_report.artifact_path"
            ),
            checked_at=_string(data.get("checked_at"), "verification_report.checked_at"),
            passed=passed,
            duration_ms=_integer(
                data.get("duration_ms"), "verification_report.duration_ms"
            ),
            width=_integer(data.get("width"), "verification_report.width"),
            height=_integer(data.get("height"), "verification_report.height"),
            frame_rate=_number(
                data.get("frame_rate"), "verification_report.frame_rate"
            ),
            video_codec=_string(
                data.get("video_codec"), "verification_report.video_codec"
            ),
            audio_codec=_optional_string(
                data.get("audio_codec"), "verification_report.audio_codec"
            ),
            pixel_format=_optional_string(
                data.get("pixel_format"), "verification_report.pixel_format"
            ),
            checks=checks,
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
    manually_edited: bool = False
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
            "manually_edited": self.manually_edited,
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
            manually_edited=_boolean(
                data.get("manually_edited", False), "selection.manually_edited"
            ),
        )
