"""Helpers for post-quiz study video recommendations."""

from __future__ import annotations

import re
from typing import Any


def _normalize_token(value: Any) -> str:
    """Normalize text for fuzzy concept matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _extract_match_terms(item: dict[str, Any]) -> set[str]:
    """Extract normalized terms from a wrong-answer item."""
    terms: set[str] = set()
    for key in ("concept", "question_text"):
        normalized = _normalize_token(item.get(key))
        if normalized:
            terms.add(normalized)

    for phrase in list(terms):
        terms.update(part for part in phrase.split() if len(part) >= 3)
    return terms


def load_video_catalog(chapter_data: dict[str, Any], chapter_id: int) -> list[dict[str, Any]]:
    """Load a chapter video catalog from Firestore."""
    catalog = chapter_data.get("study_videos")
    if isinstance(catalog, list):
        return catalog
    return []


def get_recommended_videos(
    chapter_data: dict[str, Any],
    chapter_id: int,
    wrong_questions: list[dict[str, Any]] | None,
    max_results: int = 3,
) -> list[dict[str, Any]]:
    """Return chapter videos relevant to the learner's incorrect quiz answers."""
    video_catalog = load_video_catalog(chapter_data, chapter_id)
    if not video_catalog or not wrong_questions:
        return []

    wrong_question_indices = {
        int(item["question_index"])
        for item in wrong_questions
        if isinstance(item, dict) and str(item.get("question_index", "")).isdigit()
    }
    wrong_terms: set[str] = set()
    for item in wrong_questions:
        if isinstance(item, dict):
            wrong_terms.update(_extract_match_terms(item))

    scored_videos: list[tuple[int, int, dict[str, Any]]] = []
    for position, raw_video in enumerate(video_catalog):
        if not isinstance(raw_video, dict):
            continue

        title = str(raw_video.get("title", "")).strip()
        url = str(raw_video.get("url", "")).strip()
        if not title or not url:
            continue

        concept = str(raw_video.get("concept", "")).strip()
        description = str(raw_video.get("description", "")).strip()
        concept_tags = raw_video.get("concept_tags", [])
        question_indices = raw_video.get("question_indices", [])

        normalized_video_terms = {
            _normalize_token(title),
            _normalize_token(concept),
            _normalize_token(description),
        }
        normalized_video_terms.update(
            _normalize_token(tag) for tag in concept_tags if _normalize_token(tag)
        )

        score = 0
        for video_term in normalized_video_terms:
            if not video_term:
                continue
            if video_term in wrong_terms:
                score += 4
                continue
            video_parts = set(video_term.split())
            score += sum(1 for part in video_parts if part in wrong_terms)

        if isinstance(question_indices, list):
            score += sum(
                5
                for index in question_indices
                if isinstance(index, int) and index in wrong_question_indices
            )

        if score <= 0:
            continue

        scored_videos.append(
            (
                score,
                -position,
                {
                    "title": title,
                    "url": url,
                    "concept": concept,
                    "description": description,
                },
            )
        )

    if scored_videos:
        scored_videos.sort(reverse=True)
        return [video for _, _, video in scored_videos[:max_results]]

    return [
        {
            "title": str(video.get("title", "")).strip(),
            "url": str(video.get("url", "")).strip(),
            "concept": str(video.get("concept", "")).strip(),
            "description": str(video.get("description", "")).strip(),
        }
        for video in video_catalog[:max_results]
        if isinstance(video, dict) and str(video.get("title", "")).strip() and str(video.get("url", "")).strip()
    ]
