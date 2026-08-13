"""Explainable selection of comments and contextual OP replies."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from redditsurfer.config import SelectionConfig
from redditsurfer.domain import Comment, SelectionCandidate, SelectionResult, ThreadSnapshot
from redditsurfer.editorial.text import content_words, plain_text, similarity, word_count, words
from redditsurfer.errors import SelectionError

LOW_SIGNAL_TEXT = {
    "lol",
    "lmao",
    "same",
    "this",
    "this is the way",
    "wow",
    "yes",
    "no",
    "thanks",
    "thank you",
}
QUESTION_OPENERS = {"what", "why", "when", "where", "who", "how", "did", "do", "does", "can"}
OP_UPDATE_SIGNALS = (
    "update",
    "edit",
    "to clarify",
    "for clarity",
    "for context",
    "additional context",
    "more information",
)


@dataclass(slots=True)
class _Candidate:
    kind: Literal["comment", "op_exchange", "op_update"]
    comments: tuple[Comment, ...]
    text: str
    reddit_score: int
    order: int
    duplicate: bool = False


def select_thread(snapshot: ThreadSnapshot, config: SelectionConfig) -> SelectionResult:
    """Rank eligible comments and pack complete candidates into the comment budget."""
    if snapshot.submission.removed or not snapshot.submission.body.strip():
        raise SelectionError("The submission has no usable story body.")
    if snapshot.submission.nsfw:
        raise SelectionError("NSFW submissions are excluded by the current content policy.")

    candidates = _build_candidates(snapshot, config)
    _mark_duplicates(candidates)
    max_reddit_score = max(
        (math.log1p(max(0, item.reddit_score)) for item in candidates), default=0
    )
    post_text = f"{snapshot.submission.title} {snapshot.submission.body}"
    scored = [
        _score_candidate(candidate, post_text, config, max_reddit_score)
        for candidate in candidates
    ]
    scored.sort(key=lambda pair: (-pair[1].score, pair[0].order, pair[1].id))

    target_words = round(config.target_duration_seconds * config.words_per_minute / 60)
    comment_budget = round(target_words * config.comment_budget_fraction)
    intro_budget = max(5, round(target_words * 0.08))
    post_budget = max(0, target_words - comment_budget - intro_budget)
    remaining = comment_budget
    selected_count = 0
    selected_words = 0
    final_candidates: list[SelectionCandidate] = []
    for internal, candidate in scored:
        can_select = (
            not internal.duplicate
            and selected_count < config.max_selected_candidates
            and candidate.word_count <= remaining
        )
        if can_select:
            remaining -= candidate.word_count
            selected_words += candidate.word_count
            selected_count += 1
        final_candidates.append(
            SelectionCandidate(
                id=candidate.id,
                kind=candidate.kind,
                source_ids=candidate.source_ids,
                score=candidate.score,
                component_scores=candidate.component_scores,
                penalties=candidate.penalties,
                reason_codes=candidate.reason_codes,
                word_count=candidate.word_count,
                selected=can_select,
            )
        )

    warnings: list[str] = []
    if not final_candidates:
        warnings.append("No eligible comments were found; the script will use the post only.")
    elif not any(candidate.selected for candidate in final_candidates):
        warnings.append("No complete comment candidate fits the configured duration budget.")
    if word_count(snapshot.submission.body) > post_budget:
        warnings.append(
            "The post exceeds its duration budget and will require sentence-level editing."
        )

    return SelectionResult(
        thread_id=snapshot.submission.id,
        target_duration_seconds=config.target_duration_seconds,
        target_words=target_words,
        post_word_budget=post_budget,
        comment_word_budget=comment_budget,
        selected_comment_words=selected_words,
        candidates=tuple(final_candidates),
        warnings=tuple(warnings),
    )


def _build_candidates(snapshot: ThreadSnapshot, config: SelectionConfig) -> list[_Candidate]:
    eligible = {
        comment.id: comment
        for comment in snapshot.comments
        if _eligible(comment, config)
    }
    children: dict[str, list[Comment]] = {}
    for comment in eligible.values():
        children.setdefault(comment.parent_id, []).append(comment)
    for values in children.values():
        values.sort(key=lambda item: item.order)

    candidates: list[_Candidate] = []
    used_op_replies: set[str] = set()
    for comment in sorted(eligible.values(), key=lambda item: item.order):
        if comment.is_op:
            continue
        op_replies = [reply for reply in children.get(comment.id, []) if reply.is_op]
        if op_replies:
            reply = max(op_replies, key=lambda item: (item.score, -item.order))
            used_op_replies.add(reply.id)
            comments = (comment, reply)
            candidates.append(
                _Candidate(
                    kind="op_exchange",
                    comments=comments,
                    text=" ".join(item.body for item in comments),
                    reddit_score=max(0, comment.score) + max(0, reply.score),
                    order=comment.order,
                )
            )
        else:
            candidates.append(
                _Candidate(
                    kind="comment",
                    comments=(comment,),
                    text=comment.body,
                    reddit_score=comment.score,
                    order=comment.order,
                )
            )

    for comment in sorted(eligible.values(), key=lambda item: item.order):
        if (
            comment.is_op
            and comment.id not in used_op_replies
            and comment.parent_id == snapshot.submission.id
            and _is_op_update(comment.body)
        ):
            candidates.append(
                _Candidate(
                    kind="op_update",
                    comments=(comment,),
                    text=comment.body,
                    reddit_score=comment.score,
                    order=comment.order,
                )
            )
    return candidates


def _eligible(comment: Comment, config: SelectionConfig) -> bool:
    cleaned = plain_text(comment.body)
    count = word_count(cleaned)
    return (
        not comment.removed
        and not comment.is_bot
        and comment.author_id is not None
        and comment.depth <= config.max_comment_depth
        and count >= 3
        and bool(content_words(cleaned))
    )


def _is_op_update(body: str) -> bool:
    normalized = plain_text(body).casefold()
    return any(signal in normalized for signal in OP_UPDATE_SIGNALS)


def _mark_duplicates(candidates: list[_Candidate]) -> None:
    prior_text: list[str] = []
    for candidate in sorted(candidates, key=lambda item: item.order):
        initiating_text = candidate.comments[0].body
        candidate.duplicate = any(
            similarity(initiating_text, previous) >= 0.82 for previous in prior_text
        )
        prior_text.append(initiating_text)


def _score_candidate(
    candidate: _Candidate,
    post_text: str,
    config: SelectionConfig,
    max_reddit_score: float,
) -> tuple[_Candidate, SelectionCandidate]:
    candidate_words = words(candidate.text)
    candidate_content = content_words(candidate.text)
    post_content = content_words(post_text)
    reddit = (
        math.log1p(max(0, candidate.reddit_score)) / max_reddit_score
        if max_reddit_score > 0
        else 0.0
    )
    relevance = len(candidate_content & post_content) / max(1, len(candidate_content))
    unique_ratio = len(candidate_content) / max(1, len(candidate_words))
    information = min(1.0, len(candidate_content) / 12) * min(1.0, unique_ratio / 0.6)
    first_word = candidate_words[0] if candidate_words else ""
    question = 1.0 if "?" in candidate.text or first_word in QUESTION_OPENERS else 0.0
    op_bonus = 1.0 if candidate.kind == "op_exchange" else 0.0
    early = 1 / (1 + candidate.order / 30)

    normalized_text = plain_text(candidate.text).casefold().rstrip(".!?")
    low_signal = 0.35 if normalized_text in LOW_SIGNAL_TEXT or len(candidate_words) < 6 else 0.0
    excessive = (
        min(0.4, (len(candidate_words) - config.max_candidate_words) / 500)
        if len(candidate_words) > config.max_candidate_words
        else 0.0
    )
    duplicate = 0.6 if candidate.duplicate else 0.0
    score = (
        0.30 * reddit
        + 0.20 * relevance
        + 0.15 * information
        + 0.10 * question
        + 0.20 * op_bonus
        + 0.05 * early
        - duplicate
        - low_signal
        - excessive
    )

    reasons: list[str] = []
    if reddit >= 0.65:
        reasons.append("high_reddit_score")
    if relevance >= 0.15:
        reasons.append("relevant_to_post")
    if information >= 0.55:
        reasons.append("information_dense")
    if question:
        reasons.append("question_or_clarification")
    if op_bonus:
        reasons.append("direct_op_reply")
        reasons.append("parent_context_included")
    if candidate.kind == "op_update":
        reasons.append("standalone_op_update")
    if candidate.duplicate:
        reasons.append("duplicate_candidate")
    if low_signal:
        reasons.append("low_signal")
    if excessive:
        reasons.append("excessive_length")

    result = SelectionCandidate(
        id="candidate:" + "+".join(comment.id for comment in candidate.comments),
        kind=candidate.kind,
        source_ids=tuple(comment.id for comment in candidate.comments),
        score=round(score, 6),
        component_scores={
            "reddit_score": round(reddit, 6),
            "relevance_to_post": round(relevance, 6),
            "information_density": round(information, 6),
            "question_or_clarification": question,
            "op_reply_bonus": op_bonus,
            "early_thread_bonus": round(early, 6),
        },
        penalties={
            "duplicate": duplicate,
            "low_signal": low_signal,
            "excessive_length": round(excessive, 6),
        },
        reason_codes=tuple(reasons),
        word_count=len(candidate_words),
        selected=False,
    )
    return candidate, result
