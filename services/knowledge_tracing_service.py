"""Knowledge tracing and lightweight personalization service."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
from typing import Any

from services.concept_graph import (
    CONCEPT_GRAPH,
    get_concept,
    get_concepts_for_chapter,
    get_concepts_for_subchapter,
    get_prerequisites,
    get_related_concepts,
    infer_concepts_for_quiz_question,
    infer_concepts_from_text,
    normalize_concept_label,
)


DEFAULT_MASTERY = 0.0
WEAK_THRESHOLD = 0.45
STRONG_THRESHOLD = 0.75
EVIDENCE_LIMIT = 8
HISTORY_LIMIT = 100
RECOMMENDATION_LIMIT = 5

BASE_DELTAS: dict[str, dict[str, float]] = {
    "chapter_read": {"default": 0.03},
    "quiz_answer": {"correct": 0.08, "incorrect": -0.05},
    "quiz_completed": {"default": 0.02},
    "flashcard_answer": {"correct": 0.05, "incorrect": -0.03},
    "flashcard_level_completed": {"default": 0.05},
    "video_completed": {"default": 0.04},
    "video_checkpoint_answer": {"correct": 0.05, "incorrect": -0.02},
    "chat_practice_answer": {"correct": 0.04, "incorrect": -0.02},
    "fsm_practice_answer": {"correct": 0.07, "incorrect": -0.04},
    "regex_practice_answer": {"correct": 0.07, "incorrect": -0.04},
}

DIFFICULTY_MODIFIERS = {
    "easy": 0.8,
    "medium": 1.0,
    "hard": 1.2,
    "master": 1.4,
}

EVENT_ACTIVITY_MAP = {
    "chapter_read": "lesson review",
    "quiz_answer": "quiz",
    "quiz_completed": "quiz",
    "flashcard_answer": "flashcards",
    "flashcard_level_completed": "flashcards",
    "video_completed": "video",
    "video_checkpoint_answer": "video checkpoint",
    "chat_practice_answer": "chat practice",
    "fsm_practice_answer": "practice",
    "regex_practice_answer": "practice",
}


def _timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _trim_list(items: list[Any], limit: int) -> list[Any]:
    if len(items) <= limit:
        return items
    return items[:limit]


def default_knowledge_tracing_state() -> dict[str, Any]:
    return {
        "concept_mastery": initialize_concept_mastery(),
        "learning_history": [],
        "weak_concepts": [],
        "developing_concepts": [],
        "strong_concepts": [],
        "recommended_next_concepts": [],
        "last_updated": None,
    }


def initialize_concept_mastery() -> dict[str, dict[str, Any]]:
    return {
        concept_id: {
            "mastery": DEFAULT_MASTERY,
            "confidence": 0.0,
            "attempts": 0,
            "correct": 0,
            "last_updated": None,
            "evidence": [],
        }
        for concept_id in CONCEPT_GRAPH
    }


class KnowledgeTracingService:
    """Tracks concept mastery state and builds personalization outputs."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def get_or_initialize_knowledge_state(self, user_id: str) -> dict[str, Any]:
        if not user_id:
            raise ValueError("Missing user_id for knowledge tracing.")

        user_ref = self.db.collection("Users").document(user_id)
        user_doc = user_ref.get()
        user_data = user_doc.to_dict() if user_doc.exists else {}
        state = self._normalize_state(user_data.get("knowledge_tracing"))

        if not isinstance(user_data.get("knowledge_tracing"), dict):
            user_ref.set({"knowledge_tracing": state}, merge=True)
        return state

    def update_mastery_for_event(self, user_id: str, event: dict[str, Any]) -> dict[str, Any]:
        if not user_id:
            raise ValueError("Missing user_id for knowledge tracing update.")

        user_ref = self.db.collection("Users").document(user_id)
        state = self.get_or_initialize_knowledge_state(user_id)
        result = self._apply_event_to_state(state, event)
        user_ref.set({"knowledge_tracing": state}, merge=True)
        result["state"] = state
        return result

    def update_mastery_for_events(self, user_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        if not user_id:
            raise ValueError("Missing user_id for knowledge tracing update.")

        state = self.get_or_initialize_knowledge_state(user_id)
        results: list[dict[str, Any]] = []
        for event in events:
            try:
                results.append(self._apply_event_to_state(state, event))
            except Exception as exc:
                logging.error("Knowledge tracing event failed for user=%s: %s", user_id, exc)

        self.db.collection("Users").document(user_id).set({"knowledge_tracing": state}, merge=True)
        return {"results": results, "state": state}

    def update_concept_mastery(
        self,
        state: dict[str, Any],
        concept_id: str,
        delta: float,
        evidence: dict[str, Any],
        *,
        count_attempt: bool = True,
        count_correct: bool = False,
    ) -> dict[str, float]:
        normalized_id = normalize_concept_label(concept_id)
        if not normalized_id or normalized_id not in state["concept_mastery"]:
            raise ValueError(f"Unknown concept id: {concept_id}")

        concept_state = state["concept_mastery"][normalized_id]
        before = round(_safe_float(concept_state.get("mastery", 0.0)), 4)
        after = round(_clamp(before + delta), 4)
        concept_state["mastery"] = after

        if count_attempt:
            concept_state["attempts"] = _safe_int(concept_state.get("attempts", 0)) + 1
        if count_correct:
            concept_state["correct"] = _safe_int(concept_state.get("correct", 0)) + 1

        concept_state["confidence"] = round(
            _clamp(_safe_int(concept_state.get("attempts", 0)) / 10.0),
            4,
        )
        concept_state["last_updated"] = evidence["timestamp"]

        evidence_entry = {
            "timestamp": evidence["timestamp"],
            "event_type": evidence.get("event_type"),
            "source": evidence.get("source"),
            "delta": round(delta, 4),
            "correct": evidence.get("correct"),
            "difficulty": evidence.get("difficulty"),
            "propagated": bool(evidence.get("propagated", False)),
        }
        concept_state["evidence"] = _trim_list(
            [evidence_entry] + [item for item in concept_state.get("evidence", []) if isinstance(item, dict)],
            EVIDENCE_LIMIT,
        )
        return {"before": before, "after": after}

    def recompute_weak_and_strong_concepts(self, state: dict[str, Any]) -> dict[str, Any]:
        concept_mastery = state.get("concept_mastery", {})
        weak: list[tuple[str, float]] = []
        developing: list[tuple[str, float]] = []
        strong: list[tuple[str, float]] = []

        for concept_id, payload in concept_mastery.items():
            mastery = _safe_float(payload.get("mastery", 0.0))
            if mastery < WEAK_THRESHOLD:
                weak.append((concept_id, mastery))
            elif mastery < STRONG_THRESHOLD:
                developing.append((concept_id, mastery))
            else:
                strong.append((concept_id, mastery))

        weak.sort(key=lambda item: (item[1], item[0]))
        developing.sort(key=lambda item: (item[1], item[0]))
        strong.sort(key=lambda item: (-item[1], item[0]))

        state["weak_concepts"] = [concept_id for concept_id, _ in weak]
        state["developing_concepts"] = [concept_id for concept_id, _ in developing]
        state["strong_concepts"] = [concept_id for concept_id, _ in strong]
        logging.info(
            "Knowledge tracing recomputed tiers: weak=%s strong=%s",
            state["weak_concepts"][:5],
            state["strong_concepts"][:5],
        )
        return state

    def recommend_next_concepts(
        self,
        state: dict[str, Any],
        current_chapter_id: str | None = None,
    ) -> list[dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        seen: set[str] = set()
        concept_mastery = state.get("concept_mastery", {})
        chapter_targets = get_concepts_for_chapter(current_chapter_id) if current_chapter_id else []

        prioritized_review = list(chapter_targets) + list(state.get("weak_concepts", []))
        for concept_id in prioritized_review:
            if concept_id in seen or concept_id not in concept_mastery:
                continue
            mastery = _safe_float(concept_mastery[concept_id].get("mastery", 0.0))
            if mastery >= WEAK_THRESHOLD:
                continue
            recommendations.append(
                {
                    "concept": concept_id,
                    "concept_name": CONCEPT_GRAPH[concept_id]["name"],
                    "reason": (
                        "This concept is part of the current study area and still needs reinforcement."
                        if concept_id in chapter_targets
                        else "Recent performance signals show this concept is still fragile."
                    ),
                    "recommended_activity": self._activity_for_concept(concept_id, mastery, review_mode=True),
                    "priority": "high" if concept_id in chapter_targets else "medium",
                    "mastery": round(mastery, 2),
                    "category": CONCEPT_GRAPH[concept_id]["category"],
                }
            )
            seen.add(concept_id)
            if len(recommendations) >= RECOMMENDATION_LIMIT:
                break

        if len(recommendations) < RECOMMENDATION_LIMIT:
            next_candidates: list[tuple[str, float, float]] = []
            for concept_id in CONCEPT_GRAPH:
                if concept_id in seen:
                    continue
                mastery = _safe_float(concept_mastery.get(concept_id, {}).get("mastery", 0.0))
                prereqs = get_prerequisites(concept_id)
                if not prereqs:
                    prereq_ready = 1.0
                else:
                    prereq_ready = sum(
                        _safe_float(concept_mastery.get(prereq, {}).get("mastery", 0.0))
                        for prereq in prereqs
                    ) / len(prereqs)
                if prereq_ready >= 0.6 and mastery < STRONG_THRESHOLD:
                    next_candidates.append((concept_id, prereq_ready, mastery))

            next_candidates.sort(key=lambda item: (-item[1], item[2], item[0]))
            for concept_id, prereq_ready, mastery in next_candidates:
                recommendations.append(
                    {
                        "concept": concept_id,
                        "concept_name": CONCEPT_GRAPH[concept_id]["name"],
                        "reason": "Most prerequisite ideas are in place, so this is a good next concept to consolidate.",
                        "recommended_activity": self._activity_for_concept(concept_id, mastery, review_mode=False),
                        "priority": "medium" if prereq_ready >= 0.8 else "low",
                        "mastery": round(mastery, 2),
                        "category": CONCEPT_GRAPH[concept_id]["category"],
                    }
                )
                if len(recommendations) >= RECOMMENDATION_LIMIT:
                    break

        state["recommended_next_concepts"] = recommendations
        logging.info(
            "Knowledge tracing recommendations generated: %s",
            [item["concept"] for item in recommendations],
        )
        return recommendations

    def build_knowledge_dashboard_payload(self, user_id: str) -> dict[str, Any]:
        state = self.get_or_initialize_knowledge_state(user_id)
        recommendations = self.recommend_next_concepts(state)
        return self._build_dashboard_payload_from_state(state, recommendations)

    def build_dashboard_payload_from_state(
        self,
        state: dict[str, Any],
        recommendations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        recommendation_list = recommendations if recommendations is not None else state.get("recommended_next_concepts", [])
        return self._build_dashboard_payload_from_state(state, recommendation_list)

    def get_personalization_context(self, user_id: str) -> dict[str, Any]:
        state = self.get_or_initialize_knowledge_state(user_id)
        recommendations = self.recommend_next_concepts(state)
        concept_mastery = state.get("concept_mastery", {})
        return {
            "weak_concepts": [self._concept_summary(concept_id, concept_mastery) for concept_id in state.get("weak_concepts", [])[:5]],
            "strong_concepts": [self._concept_summary(concept_id, concept_mastery) for concept_id in state.get("strong_concepts", [])[:5]],
            "recommended_next_concepts": recommendations,
            "concept_mastery": {
                concept_id: round(_safe_float(payload.get("mastery", 0.0)), 4)
                for concept_id, payload in concept_mastery.items()
            },
        }

    def _normalize_state(self, raw_state: Any) -> dict[str, Any]:
        normalized = default_knowledge_tracing_state()
        incoming = raw_state if isinstance(raw_state, dict) else {}
        incoming_mastery = incoming.get("concept_mastery", {})
        if not isinstance(incoming_mastery, dict):
            incoming_mastery = {}

        for concept_id in CONCEPT_GRAPH:
            stored_payload = incoming_mastery.get(concept_id, {})
            if not isinstance(stored_payload, dict):
                stored_payload = {}
            attempts = _safe_int(stored_payload.get("attempts", 0))
            normalized["concept_mastery"][concept_id] = {
                "mastery": round(_clamp(_safe_float(stored_payload.get("mastery", DEFAULT_MASTERY))), 4),
                "confidence": round(_clamp(_safe_float(stored_payload.get("confidence", attempts / 10.0))), 4),
                "attempts": attempts,
                "correct": _safe_int(stored_payload.get("correct", 0)),
                "last_updated": stored_payload.get("last_updated"),
                "evidence": [
                    item for item in stored_payload.get("evidence", []) if isinstance(item, dict)
                ][:EVIDENCE_LIMIT],
            }

        normalized["learning_history"] = [
            item for item in incoming.get("learning_history", []) if isinstance(item, dict)
        ][:HISTORY_LIMIT]
        normalized["last_updated"] = incoming.get("last_updated")
        self.recompute_weak_and_strong_concepts(normalized)
        normalized["recommended_next_concepts"] = list(incoming.get("recommended_next_concepts", []))
        return normalized

    def _apply_event_to_state(self, state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("event_type", "")).strip()
        if event_type not in BASE_DELTAS:
            raise ValueError(f"Unsupported knowledge tracing event: {event_type}")

        concepts = self._resolve_event_concepts(event)
        if not concepts:
            logging.info("Knowledge tracing skipped event=%s because no concepts were resolved.", event_type)
            return {"event_type": event_type, "updated_concepts": [], "mastery_changes": {}}

        delta = self._base_delta_for_event(event)
        if delta == 0.0:
            return {"event_type": event_type, "updated_concepts": concepts, "mastery_changes": {}}

        timestamp = _timestamp()
        mastery_changes: dict[str, dict[str, float]] = {}
        updated_concepts: list[str] = []
        evidence = {
            "timestamp": timestamp,
            "event_type": event_type,
            "source": event.get("source") or event_type,
            "correct": event.get("correct"),
            "difficulty": str(event.get("difficulty", "medium")).strip().lower() or "medium",
            "chapter_id": event.get("chapter_id"),
            "subchapter_id": event.get("subchapter_id"),
        }

        for concept_id in concepts:
            try:
                change = self.update_concept_mastery(
                    state,
                    concept_id,
                    delta,
                    evidence,
                    count_attempt=event_type != "chapter_read",
                    count_correct=bool(event.get("correct")),
                )
            except ValueError:
                continue
            mastery_changes[concept_id] = change
            updated_concepts.append(concept_id)

            prereq_delta = round(delta * 0.2, 4)
            for prereq_id in get_prerequisites(concept_id):
                try:
                    self.update_concept_mastery(
                        state,
                        prereq_id,
                        prereq_delta,
                        {**evidence, "propagated": True},
                        count_attempt=False,
                        count_correct=False,
                    )
                except ValueError:
                    continue

            if delta > 0:
                related_delta = round(delta * 0.1, 4)
                for related_id in get_related_concepts(concept_id):
                    try:
                        self.update_concept_mastery(
                            state,
                            related_id,
                            related_delta,
                            {**evidence, "propagated": True},
                            count_attempt=False,
                            count_correct=False,
                        )
                    except ValueError:
                        continue

        if updated_concepts:
            self._append_history_entry(state, event, timestamp, mastery_changes)
            self.recompute_weak_and_strong_concepts(state)
            self.recommend_next_concepts(state, current_chapter_id=str(event.get("chapter_id") or "") or None)
            state["last_updated"] = timestamp

        logging.info(
            "Knowledge tracing processed event=%s concepts=%s delta=%.4f",
            event_type,
            updated_concepts,
            delta,
        )
        return {
            "event_type": event_type,
            "updated_concepts": updated_concepts,
            "mastery_changes": mastery_changes,
            "recommendations": state.get("recommended_next_concepts", []),
        }

    def _append_history_entry(
        self,
        state: dict[str, Any],
        event: dict[str, Any],
        timestamp: str,
        mastery_changes: dict[str, dict[str, float]],
    ) -> None:
        entry = {
            "timestamp": timestamp,
            "event_type": event.get("event_type"),
            "concepts": list(mastery_changes.keys()),
            "mastery_changes": mastery_changes,
            "source": event.get("source"),
            "chapter_id": str(event.get("chapter_id")) if event.get("chapter_id") is not None else None,
        }
        state["learning_history"] = _trim_list(
            [entry] + [item for item in state.get("learning_history", []) if isinstance(item, dict)],
            HISTORY_LIMIT,
        )

    def _resolve_event_concepts(self, event: dict[str, Any]) -> list[str]:
        explicit = event.get("concepts")
        candidates: list[str] = []
        if isinstance(explicit, str):
            candidates = [explicit]
        elif isinstance(explicit, list):
            candidates = [str(item) for item in explicit if str(item).strip()]

        resolved: list[str] = []
        for candidate in candidates:
            normalized = normalize_concept_label(candidate)
            if normalized and normalized not in resolved:
                resolved.append(normalized)
                continue
            for inferred in infer_concepts_from_text(candidate):
                if inferred not in resolved:
                    resolved.append(inferred)

        if resolved:
            return resolved

        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        chapter_id = event.get("chapter_id")
        subchapter_id = event.get("subchapter_id")

        for text_key in ("question_text", "text", "title", "description", "prompt"):
            text_value = metadata.get(text_key) or event.get(text_key)
            inferred = infer_concepts_from_text(text_value)
            for concept_id in inferred:
                if concept_id not in resolved:
                    resolved.append(concept_id)

        if not resolved and subchapter_id:
            for concept_id in get_concepts_for_subchapter(str(subchapter_id)):
                if concept_id not in resolved:
                    resolved.append(concept_id)

        if not resolved and chapter_id is not None:
            for concept_id in get_concepts_for_chapter(chapter_id):
                if concept_id not in resolved:
                    resolved.append(concept_id)

        if not resolved and metadata.get("question_text"):
            return infer_concepts_for_quiz_question(str(metadata["question_text"]), chapter_id)
        return resolved

    def _base_delta_for_event(self, event: dict[str, Any]) -> float:
        event_type = str(event.get("event_type", "")).strip()
        event_rule = BASE_DELTAS[event_type]
        if "correct" in event_rule or "incorrect" in event_rule:
            is_correct = bool(event.get("correct"))
            base_delta = event_rule["correct"] if is_correct else event_rule["incorrect"]
        else:
            base_delta = event_rule["default"]

        difficulty_key = str(event.get("difficulty", "medium")).strip().lower() or "medium"
        difficulty_multiplier = DIFFICULTY_MODIFIERS.get(difficulty_key, 1.0)
        confidence_signal = _safe_float(event.get("confidence_signal", 1.0), 1.0)
        confidence_signal = max(0.5, min(confidence_signal, 1.2))
        return round(base_delta * difficulty_multiplier * confidence_signal, 4)

    def _concept_summary(self, concept_id: str, concept_mastery: dict[str, Any]) -> dict[str, Any]:
        payload = concept_mastery.get(concept_id, {})
        concept = CONCEPT_GRAPH.get(concept_id, {})
        return {
            "id": concept_id,
            "name": concept.get("name", concept_id.replace("_", " ").title()),
            "category": concept.get("category", "Automata Theory"),
            "mastery": round(_safe_float(payload.get("mastery", 0.0)), 2),
            "mastery_percent": round(_safe_float(payload.get("mastery", 0.0)) * 100),
            "confidence": round(_safe_float(payload.get("confidence", 0.0)), 2),
            "attempts": _safe_int(payload.get("attempts", 0)),
            "correct": _safe_int(payload.get("correct", 0)),
            "description": concept.get("description", ""),
        }

    def _activity_for_concept(self, concept_id: str, mastery: float, *, review_mode: bool) -> str:
        category = CONCEPT_GRAPH.get(concept_id, {}).get("category", "")
        if review_mode and mastery < 0.2:
            return "lesson"
        if "Complexity" in category or "Computation" in category:
            return "video" if mastery < 0.5 else "quiz"
        if concept_id in {"regex_to_nfa", "nfa_to_dfa", "dfa_minimization", "pushdown_automata"}:
            return "flashcards" if review_mode else "quiz"
        return "flashcards" if review_mode else "lesson"

    def _build_dashboard_payload_from_state(
        self,
        state: dict[str, Any],
        recommendations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        concept_mastery = state.get("concept_mastery", {})
        all_concepts = [
            self._concept_summary(concept_id, concept_mastery)
            for concept_id in concept_mastery
        ]
        all_concepts.sort(key=lambda item: (-item["mastery"], item["name"]))

        weak_concepts = [
            self._concept_summary(concept_id, concept_mastery)
            for concept_id in state.get("weak_concepts", [])[:3]
        ]
        strong_concepts = [
            self._concept_summary(concept_id, concept_mastery)
            for concept_id in state.get("strong_concepts", [])[:3]
        ]
        focus_concepts = weak_concepts + [item for item in strong_concepts if item["id"] not in {entry["id"] for entry in weak_concepts}]
        focus_concepts = focus_concepts[:6]

        history = state.get("learning_history", [])
        trend_points: list[dict[str, Any]] = []
        recent_history = list(reversed(history[:7]))
        for entry in recent_history:
            delta_total = 0.0
            for change in entry.get("mastery_changes", {}).values():
                delta_total += _safe_float(change.get("after", 0.0)) - _safe_float(change.get("before", 0.0))
            trend_points.append(
                {
                    "label": str(entry.get("event_type", "event")).replace("_", " ").title(),
                    "delta": round(delta_total, 3),
                }
            )

        average_mastery = 0.0
        if concept_mastery:
            average_mastery = sum(_safe_float(item.get("mastery", 0.0)) for item in concept_mastery.values()) / len(concept_mastery)

        return {
            "overview": {
                "tracked_concepts": len(CONCEPT_GRAPH),
                "weak_count": len(state.get("weak_concepts", [])),
                "developing_count": len(state.get("developing_concepts", [])),
                "strong_count": len(state.get("strong_concepts", [])),
                "average_mastery_percent": round(average_mastery * 100),
            },
            "top_strong_concepts": strong_concepts,
            "top_weak_concepts": weak_concepts,
            "recommendations": recommendations[:3],
            "focus_concepts": focus_concepts,
            "recent_trend": trend_points,
            "graph_placeholder": "Concept graph visualization can be layered on top of this mastery model later without changing the stored learner state.",
        }
