"""Adaptive flashcard helpers and Firestore progress management."""

from __future__ import annotations

import logging
import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


LEVELS = ["easy", "medium", "hard", "master"]


@dataclass
class FlashcardAnswerResult:
    """Result of answering a flashcard."""

    is_correct: bool
    selected_answer: str
    correct_answer: str
    hint: str
    explanation: str
    level_completed: bool
    path_completed: bool
    current_level: str


def default_progress_state() -> dict[str, Any]:
    """Create a new adaptive flashcard progress state."""
    return {
        "current_level": "easy",
        "path_completed": False,
        "levels": {
            level: {
                "completed_ids": [],
                "incorrect_ids": [],
                "selected_ids": [],
                "completed": False,
                "unlocked": level == "easy",
            }
            for level in LEVELS
        },
    }


def normalize_progress_state(progress: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize potentially partial progress data."""
    normalized = default_progress_state()
    if not progress:
        return normalized

    normalized["current_level"] = progress.get("current_level", "easy")
    normalized["path_completed"] = bool(progress.get("path_completed", False))

    incoming_levels = progress.get("levels", {})
    for level in LEVELS:
        incoming_level = incoming_levels.get(level, {})
        normalized["levels"][level]["completed_ids"] = list(incoming_level.get("completed_ids", []))
        normalized["levels"][level]["incorrect_ids"] = list(incoming_level.get("incorrect_ids", []))
        normalized["levels"][level]["selected_ids"] = list(incoming_level.get("selected_ids", []))
        normalized["levels"][level]["completed"] = bool(incoming_level.get("completed", False))
        normalized["levels"][level]["unlocked"] = bool(
            incoming_level.get("unlocked", level == "easy")
        )

    return normalized


def get_user_flashcard_progress(user_data: dict[str, Any], chapter_id: int) -> dict[str, Any]:
    """Extract and normalize a chapter's adaptive flashcard progress from a user document."""
    adaptive_progress = user_data.get("adaptive_flashcard_progress", {})
    chapter_progress = adaptive_progress.get(str(chapter_id))
    return normalize_progress_state(chapter_progress)


def build_progress_update_payload(
    user_data: dict[str, Any],
    chapter_id: int,
    chapter_progress: dict[str, Any],
) -> dict[str, Any]:
    """Build a merge-safe payload for saving progress."""
    adaptive_progress = deepcopy(user_data.get("adaptive_flashcard_progress", {}))
    adaptive_progress[str(chapter_id)] = chapter_progress
    return {"adaptive_flashcard_progress": adaptive_progress}


def sync_progress_with_flashcards(
    chapter_progress: dict[str, Any],
    flashcards_by_level: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Ensure progress state reflects level completion and unlocking."""
    progress = normalize_progress_state(chapter_progress)
    path_completed = True

    for index, level in enumerate(LEVELS):
        level_cards = flashcards_by_level.get(level, [])
        level_card_ids = {card["id"] for card in level_cards}
        level_state = progress["levels"][level]

        # Drop stale ids if the generated card set changes.
        level_state["completed_ids"] = [card_id for card_id in level_state["completed_ids"] if card_id in level_card_ids]
        level_state["incorrect_ids"] = [card_id for card_id in level_state["incorrect_ids"] if card_id in level_card_ids]
        level_state["selected_ids"] = [card_id for card_id in level_state.get("selected_ids", []) if card_id in level_card_ids]

        level_completed = bool(level_cards) and len(level_state["completed_ids"]) == len(level_cards)
        level_state["completed"] = level_completed

        if level_completed and index + 1 < len(LEVELS):
            next_level = LEVELS[index + 1]
            progress["levels"][next_level]["unlocked"] = True
        if not level_completed:
            path_completed = False

    # Move current level to the first unlocked incomplete level.
    for level in LEVELS:
        level_state = progress["levels"][level]
        if level_state["unlocked"] and not level_state["completed"]:
            progress["current_level"] = level
            break
    else:
        progress["current_level"] = "master"

    progress["path_completed"] = path_completed and progress["levels"]["master"]["completed"]
    return progress


def get_level_statuses(
    chapter_progress: dict[str, Any],
    flashcards_by_level: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build presentation-friendly level status information."""
    statuses = []
    for level in LEVELS:
        level_state = chapter_progress["levels"][level]
        total_count = len(flashcards_by_level.get(level, []))
        statuses.append(
            {
                "name": level,
                "completed": level_state["completed"],
                "unlocked": level_state["unlocked"],
                "completed_count": len(level_state["completed_ids"]),
                "remaining_count": max(0, total_count - len(level_state["completed_ids"])),
                "total_count": total_count,
            }
        )
    return statuses


def choose_next_flashcard(
    flashcards_by_level: dict[str, list[dict[str, Any]]],
    chapter_progress: dict[str, Any],
) -> dict[str, Any] | None:
    """Choose the next flashcard from the current level among incomplete cards."""
    current_level = chapter_progress["current_level"]
    level_cards = flashcards_by_level.get(current_level, [])
    level_state = chapter_progress["levels"][current_level]
    completed_ids = set(level_state["completed_ids"])
    remaining_cards = [card for card in level_cards if card["id"] not in completed_ids]

    if not remaining_cards:
        return None

    incorrect_ids = set(level_state["incorrect_ids"])
    incorrect_cards = [card for card in remaining_cards if card["id"] in incorrect_ids]
    if incorrect_cards:
        return random.choice(incorrect_cards)
    return random.choice(remaining_cards)


def answer_flashcard(
    flashcards_by_level: dict[str, list[dict[str, Any]]],
    chapter_progress: dict[str, Any],
    flashcard_id: str,
    selected_answer: str,
) -> tuple[dict[str, Any], FlashcardAnswerResult]:
    """Apply an answer and update progress."""
    progress = normalize_progress_state(chapter_progress)
    current_level = progress["current_level"]
    level_cards = flashcards_by_level.get(current_level, [])
    flashcard = next((card for card in level_cards if card["id"] == flashcard_id), None)
    if flashcard is None:
        raise ValueError("Flashcard not found in the current level.")

    level_state = progress["levels"][current_level]
    is_correct = selected_answer == flashcard["correct_answer"]

    if is_correct:
        if flashcard_id not in level_state["completed_ids"]:
            level_state["completed_ids"].append(flashcard_id)
        if flashcard_id in level_state["incorrect_ids"]:
            level_state["incorrect_ids"].remove(flashcard_id)
    else:
        if flashcard_id not in level_state["incorrect_ids"]:
            level_state["incorrect_ids"].append(flashcard_id)

    progress = sync_progress_with_flashcards(progress, flashcards_by_level)
    updated_level_state = progress["levels"][current_level]

    result = FlashcardAnswerResult(
        is_correct=is_correct,
        selected_answer=selected_answer,
        correct_answer=flashcard["correct_answer"],
        hint=flashcard.get("hint", ""),
        explanation=flashcard.get("explanation", ""),
        level_completed=updated_level_state["completed"],
        path_completed=progress["path_completed"],
        current_level=progress["current_level"],
    )
    return progress, result


def validate_adaptive_flashcards(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Validate the Firestore flashcard structure."""
    if not isinstance(payload, dict):
        raise ValueError("adaptive_flashcards must be a JSON object.")

    validated: dict[str, list[dict[str, Any]]] = {}
    seen_ids: set[str] = set()

    for level in LEVELS:
        questions = payload.get(level)
        if not isinstance(questions, list) or len(questions) != 5:
            raise ValueError(f"Difficulty '{level}' must contain exactly 5 flashcards.")

        validated[level] = []
        for index, item in enumerate(questions):
            if not isinstance(item, dict):
                raise ValueError(f"Flashcard {level}[{index}] must be an object.")

            options = item.get("options")
            correct_answer = item.get("correct_answer")
            difficulty = item.get("difficulty")
            flashcard_id = str(item.get("id", "")).strip()

            if difficulty != level:
                raise ValueError(f"Flashcard {level}[{index}] has invalid difficulty '{difficulty}'.")
            if not flashcard_id:
                raise ValueError(f"Flashcard {level}[{index}] is missing an id.")
            if flashcard_id in seen_ids:
                raise ValueError(f"Duplicate flashcard id detected: {flashcard_id}")
            if not isinstance(options, list) or len(options) != 4:
                raise ValueError(f"Flashcard {level}[{index}] must have exactly 4 options.")
            if len({str(option).strip() for option in options}) != 4:
                raise ValueError(f"Flashcard {level}[{index}] options must be distinct.")
            if correct_answer not in options:
                raise ValueError(f"Flashcard {level}[{index}] correct_answer must match one option exactly.")

            seen_ids.add(flashcard_id)
            validated[level].append(
                {
                    "id": flashcard_id,
                    "question": str(item.get("question", "")).strip(),
                    "options": [str(option).strip() for option in options],
                    "correct_answer": str(correct_answer).strip(),
                    "hint": str(item.get("hint", "")).strip(),
                    "explanation": str(item.get("explanation", "")).strip(),
                    "concept": str(item.get("concept", "")).strip(),
                    "difficulty": level,
                }
            )

    logging.info("Validated adaptive flashcards for all levels successfully.")
    return validated
