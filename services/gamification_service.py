"""Centralized gamification, progression, and dashboard helpers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from typing import Any


XP_RULES = {
    "chapter_completed": 20,
    "quiz_attempted": 10,
    "quiz_perfect": 40,
    "flashcard_correct": 5,
    "flashcard_level_completed": 25,
    "video_checkpoint_answered": 8,
    "video_completed": 30,
    "video_summary_opened": 10,
    "chat_question_answered": 6,
    "fsm_practice_completed": 20,
    "regex_practice_completed": 20,
    "daily_streak_bonus": 15,
}

DEFAULT_STATS = {
    "chapters_completed": 0,
    "quizzes_attempted": 0,
    "quizzes_perfect": 0,
    "flashcards_correct": 0,
    "flashcard_levels_completed": 0,
    "flashcard_paths_completed": 0,
    "videos_completed": 0,
    "video_checkpoints_answered": 0,
    "chat_questions_answered": 0,
    "fsm_challenges_completed": 0,
    "regex_challenges_completed": 0,
}

DAILY_GOAL_TARGETS = {
    "chapters_completed": 1,
    "quizzes_attempted": 1,
    "flashcards_correct": 5,
    "video_checkpoints_answered": 1,
}

ACTIVITY_LOG_LIMIT = 30
EVENT_KEY_LIMIT = 600
DAILY_PROGRESS_LIMIT = 21
TOTAL_CHAPTER_COUNT = 49
DEFAULT_VIDEO_TARGET = 7


BADGE_DEFINITIONS = [
    {
        "id": "first_steps",
        "name": "First Steps",
        "description": "Complete your first chapter.",
        "icon": "compass",
    },
    {
        "id": "quiz_starter",
        "name": "Quiz Starter",
        "description": "Attempt your first quiz.",
        "icon": "spark",
    },
    {
        "id": "perfect_automaton",
        "name": "Perfect Automaton",
        "description": "Earn a perfect score on a quiz.",
        "icon": "trophy",
    },
    {
        "id": "flashcard_climber",
        "name": "Flashcard Climber",
        "description": "Complete one full adaptive flashcard path.",
        "icon": "layers",
    },
    {
        "id": "video_scholar",
        "name": "Video Scholar",
        "description": "Complete three learning videos.",
        "icon": "play",
    },
    {
        "id": "consistency_i",
        "name": "Consistency I",
        "description": "Maintain a 3-day study streak.",
        "icon": "flame",
    },
    {
        "id": "consistency_ii",
        "name": "Consistency II",
        "description": "Maintain a 7-day study streak.",
        "icon": "bolt",
    },
    {
        "id": "consistency_iii",
        "name": "Consistency III",
        "description": "Maintain a 14-day study streak.",
        "icon": "crown",
    },
    {
        "id": "fsm_builder",
        "name": "FSM Builder",
        "description": "Complete your first FSM challenge.",
        "icon": "diagram",
    },
    {
        "id": "theory_grinder",
        "name": "Theory Grinder",
        "description": "Reach 1000 total XP.",
        "icon": "shield",
    },
]


@dataclass
class RewardEvent:
    event_name: str
    event_key: str
    metadata: dict[str, Any] | None = None


def default_gamification_state() -> dict[str, Any]:
    return {
        "xp_total": 0,
        "level": 1,
        "streak_days": 0,
        "last_activity_date": None,
        "badges": [],
        "activity_log": [],
        "awarded_event_keys": [],
        "daily_progress": {},
        "stats": deepcopy(DEFAULT_STATS),
    }


def _today_str() -> str:
    return date.today().isoformat()


def _timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _level_threshold(level: int) -> int:
    if level <= 1:
        return 0
    total = 0
    for current_level in range(2, level + 1):
        total += 100 + ((current_level - 2) * 50)
    return total


def calculate_level_from_xp(xp_total: int) -> int:
    level = 1
    while xp_total >= _level_threshold(level + 1):
        level += 1
    return level


def _level_progress_details(xp_total: int) -> dict[str, int | float]:
    current_level = calculate_level_from_xp(xp_total)
    current_floor = _level_threshold(current_level)
    next_floor = _level_threshold(current_level + 1)
    span = max(1, next_floor - current_floor)
    progress = max(0, xp_total - current_floor)
    return {
        "current_level": current_level,
        "current_floor_xp": current_floor,
        "next_level_xp": next_floor,
        "xp_to_next_level": max(0, next_floor - xp_total),
        "progress_percent": round((progress / span) * 100, 2),
    }


def _normalize_daily_progress(raw_progress: Any) -> dict[str, dict[str, int]]:
    if not isinstance(raw_progress, dict):
        return {}

    normalized: dict[str, dict[str, int]] = {}
    for day, payload in raw_progress.items():
        if not isinstance(payload, dict):
            continue
        normalized[str(day)] = {
            stat_name: _safe_int(payload.get(stat_name, 0))
            for stat_name in DEFAULT_STATS
        }
    return normalized


def _trim_mapping(mapping: dict[str, Any], *, limit: int) -> dict[str, Any]:
    if len(mapping) <= limit:
        return mapping
    ordered_keys = sorted(mapping.keys(), reverse=True)[:limit]
    return {key: mapping[key] for key in sorted(ordered_keys)}


def _trim_list(items: list[Any], *, limit: int) -> list[Any]:
    if len(items) <= limit:
        return items
    return items[:limit]


class GamificationService:
    """Coordinates XP, levels, streaks, badges, and dashboard payloads."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def get_or_initialize_gamification_state(self, user_doc: dict | None) -> dict[str, Any]:
        state = default_gamification_state()
        incoming = (user_doc or {}).get("gamification", {})
        if not isinstance(incoming, dict):
            incoming = {}

        state["xp_total"] = _safe_int(incoming.get("xp_total", 0))
        state["level"] = _safe_int(incoming.get("level", 1), 1)
        state["streak_days"] = _safe_int(incoming.get("streak_days", 0))
        state["last_activity_date"] = incoming.get("last_activity_date")
        state["badges"] = [
            badge for badge in incoming.get("badges", []) if isinstance(badge, dict) and badge.get("id")
        ]
        state["activity_log"] = [
            entry for entry in incoming.get("activity_log", []) if isinstance(entry, dict)
        ]
        state["awarded_event_keys"] = [
            str(item) for item in incoming.get("awarded_event_keys", []) if str(item).strip()
        ]
        state["daily_progress"] = _normalize_daily_progress(incoming.get("daily_progress", {}))

        incoming_stats = incoming.get("stats", {})
        if not isinstance(incoming_stats, dict):
            incoming_stats = {}
        for stat_name, default_value in DEFAULT_STATS.items():
            state["stats"][stat_name] = _safe_int(incoming_stats.get(stat_name, default_value))

        state["level"] = calculate_level_from_xp(state["xp_total"])
        state["daily_progress"] = _trim_mapping(state["daily_progress"], limit=DAILY_PROGRESS_LIMIT)
        state["activity_log"] = _trim_list(state["activity_log"], limit=ACTIVITY_LOG_LIMIT)
        state["awarded_event_keys"] = _trim_list(state["awarded_event_keys"], limit=EVENT_KEY_LIMIT)
        return state

    def has_awarded_event(self, gamification_state: dict[str, Any], event_key: str) -> bool:
        return str(event_key) in set(gamification_state.get("awarded_event_keys", []))

    def increment_stat(
        self,
        gamification_state: dict[str, Any],
        stat_name: str,
        amount: int = 1,
        *,
        today_str: str | None = None,
    ) -> None:
        if stat_name not in gamification_state["stats"]:
            gamification_state["stats"][stat_name] = 0
        gamification_state["stats"][stat_name] = _safe_int(gamification_state["stats"].get(stat_name, 0)) + amount

        day_key = today_str or _today_str()
        daily_progress = gamification_state.setdefault("daily_progress", {})
        day_payload = daily_progress.setdefault(day_key, {name: 0 for name in DEFAULT_STATS})
        day_payload[stat_name] = _safe_int(day_payload.get(stat_name, 0)) + amount
        gamification_state["daily_progress"] = _trim_mapping(daily_progress, limit=DAILY_PROGRESS_LIMIT)

    def update_streak(self, gamification_state: dict[str, Any], today_str: str) -> dict[str, Any]:
        previous_date = gamification_state.get("last_activity_date")
        previous_streak = _safe_int(gamification_state.get("streak_days", 0))

        if previous_date == today_str:
            return {
                "state": gamification_state,
                "streak_changed": False,
                "bonus_awarded": False,
            }

        if previous_date:
            previous = date.fromisoformat(str(previous_date))
            today = date.fromisoformat(today_str)
            day_delta = (today - previous).days
            if day_delta == 1:
                gamification_state["streak_days"] = max(1, previous_streak + 1)
            else:
                gamification_state["streak_days"] = 1
        else:
            gamification_state["streak_days"] = 1

        gamification_state["last_activity_date"] = today_str
        streak_bonus_key = f"streak:{today_str}:maintained"
        bonus_awarded = False
        if (
            gamification_state["streak_days"] >= 2
            and streak_bonus_key not in gamification_state["awarded_event_keys"]
        ):
            gamification_state["xp_total"] += XP_RULES["daily_streak_bonus"]
            gamification_state["awarded_event_keys"].append(streak_bonus_key)
            self._add_activity_entry(
                gamification_state,
                {
                    "type": "streak_bonus",
                    "label": f"Maintained a {gamification_state['streak_days']}-day streak",
                    "timestamp": _timestamp(),
                    "meta": {"streak_days": gamification_state["streak_days"]},
                },
            )
            bonus_awarded = True

        gamification_state["level"] = calculate_level_from_xp(gamification_state["xp_total"])
        return {
            "state": gamification_state,
            "streak_changed": True,
            "bonus_awarded": bonus_awarded,
        }

    def evaluate_badges(self, gamification_state: dict[str, Any]) -> list[dict[str, Any]]:
        unlocked = {badge.get("id") for badge in gamification_state.get("badges", [])}
        stats = gamification_state.get("stats", {})
        streak_days = _safe_int(gamification_state.get("streak_days", 0))
        xp_total = _safe_int(gamification_state.get("xp_total", 0))
        now = _timestamp()
        new_badges: list[dict[str, Any]] = []

        def unlock(badge_id: str) -> None:
            if badge_id in unlocked:
                return
            definition = next((item for item in BADGE_DEFINITIONS if item["id"] == badge_id), None)
            if not definition:
                return
            badge = {
                "id": definition["id"],
                "name": definition["name"],
                "description": definition["description"],
                "icon": definition["icon"],
                "unlocked_at": now,
            }
            gamification_state["badges"].append(badge)
            unlocked.add(badge_id)
            new_badges.append(badge)
            self._add_activity_entry(
                gamification_state,
                {
                    "type": "badge_unlocked",
                    "label": f"Earned {definition['name']}",
                    "timestamp": now,
                    "meta": {"badge_id": badge_id},
                },
            )

        if _safe_int(stats.get("chapters_completed", 0)) >= 1:
            unlock("first_steps")
        if _safe_int(stats.get("quizzes_attempted", 0)) >= 1:
            unlock("quiz_starter")
        if _safe_int(stats.get("quizzes_perfect", 0)) >= 1:
            unlock("perfect_automaton")
        if _safe_int(stats.get("flashcard_paths_completed", 0)) >= 1:
            unlock("flashcard_climber")
        if _safe_int(stats.get("videos_completed", 0)) >= 3:
            unlock("video_scholar")
        if streak_days >= 3:
            unlock("consistency_i")
        if streak_days >= 7:
            unlock("consistency_ii")
        if streak_days >= 14:
            unlock("consistency_iii")
        if _safe_int(stats.get("fsm_challenges_completed", 0)) >= 1:
            unlock("fsm_builder")
        if xp_total >= 1000:
            unlock("theory_grinder")

        return new_badges

    def award_event_xp(
        self,
        user_id: str,
        event_name: str,
        event_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.apply_events(user_id, [RewardEvent(event_name, event_key, metadata)])

    def apply_events(self, user_id: str, events: list[RewardEvent]) -> dict[str, Any]:
        user_ref = self.db.collection("Users").document(user_id)
        user_doc = user_ref.get()
        user_data = user_doc.to_dict() if user_doc.exists else {}
        state = self.get_or_initialize_gamification_state(user_data)
        today_str = _today_str()

        pre_xp = _safe_int(state["xp_total"])
        pre_level = _safe_int(state["level"], 1)
        feedback_items: list[dict[str, Any]] = []

        streak_result = self.update_streak(state, today_str)
        if streak_result["bonus_awarded"]:
            feedback_items.append(
                {
                    "type": "xp",
                    "label": f"+{XP_RULES['daily_streak_bonus']} XP",
                    "detail": f"Daily streak bonus for {state['streak_days']} consecutive study days.",
                }
            )

        for event in events:
            if self.has_awarded_event(state, event.event_key):
                continue

            xp_award = XP_RULES.get(event.event_name, 0)
            if xp_award:
                state["xp_total"] += xp_award
                feedback_items.append(
                    {
                        "type": "xp",
                        "label": f"+{xp_award} XP",
                        "detail": self._event_feedback_detail(event.event_name, event.metadata or {}),
                    }
                )

            state["awarded_event_keys"].append(str(event.event_key))
            self._apply_event_stats(state, event, today_str=today_str)
            self._add_activity_entry(
                state,
                self._build_activity_entry(event.event_name, event.metadata or {}),
            )

        state["level"] = calculate_level_from_xp(state["xp_total"])
        new_badges = self.evaluate_badges(state)
        if state["level"] > pre_level:
            feedback_items.append(
                {
                    "type": "level_up",
                    "label": f"Level Up! Level {state['level']}",
                    "detail": "Your automata theory rank just increased.",
                }
            )

        for badge in new_badges:
            feedback_items.append(
                {
                    "type": "badge",
                    "label": f"Badge Unlocked: {badge['name']}",
                    "detail": badge["description"],
                }
            )

        state["awarded_event_keys"] = _trim_list(state["awarded_event_keys"], limit=EVENT_KEY_LIMIT)
        state["activity_log"] = _trim_list(state["activity_log"], limit=ACTIVITY_LOG_LIMIT)
        state["daily_progress"] = _trim_mapping(state["daily_progress"], limit=DAILY_PROGRESS_LIMIT)

        user_ref.set({"gamification": state}, merge=True)
        updated_user_data = dict(user_data)
        updated_user_data["gamification"] = state

        return {
            "xp_gained": max(0, _safe_int(state["xp_total"]) - pre_xp),
            "new_total_xp": _safe_int(state["xp_total"]),
            "new_level": _safe_int(state["level"], 1),
            "level_up": state["level"] > pre_level,
            "new_badges": new_badges,
            "updated_streak": _safe_int(state["streak_days"]),
            "feedback_items": feedback_items,
            "dashboard_payload": self.build_dashboard_payload(updated_user_data),
        }

    def combine_results(self, results: list[dict[str, Any] | None], user_doc: dict | None = None) -> dict[str, Any]:
        combined = {
            "xp_gained": 0,
            "new_total_xp": 0,
            "new_level": 1,
            "level_up": False,
            "new_badges": [],
            "updated_streak": 0,
            "feedback_items": [],
            "dashboard_payload": self.build_dashboard_payload(user_doc or {}),
        }

        seen_badges: set[str] = set()
        dashboard_payload = combined["dashboard_payload"]
        for result in results:
            if not result:
                continue
            combined["xp_gained"] += _safe_int(result.get("xp_gained", 0))
            combined["new_total_xp"] = _safe_int(result.get("new_total_xp", combined["new_total_xp"]))
            combined["new_level"] = _safe_int(result.get("new_level", combined["new_level"]))
            combined["level_up"] = combined["level_up"] or bool(result.get("level_up"))
            combined["updated_streak"] = _safe_int(result.get("updated_streak", combined["updated_streak"]))
            combined["feedback_items"].extend(result.get("feedback_items", []))
            for badge in result.get("new_badges", []):
                badge_id = str(badge.get("id", ""))
                if badge_id and badge_id not in seen_badges:
                    seen_badges.add(badge_id)
                    combined["new_badges"].append(badge)
            if result.get("dashboard_payload"):
                dashboard_payload = result["dashboard_payload"]

        combined["dashboard_payload"] = dashboard_payload
        return combined

    def build_dashboard_payload(self, user_doc: dict | None) -> dict[str, Any]:
        user_doc = user_doc or {}
        state = self.get_or_initialize_gamification_state(user_doc)
        xp_details = _level_progress_details(_safe_int(state["xp_total"]))
        today_str = _today_str()
        today_progress = state.get("daily_progress", {}).get(today_str, {})

        unlocked_badge_ids = {badge.get("id") for badge in state.get("badges", [])}
        unlocked_badges = sorted(
            state.get("badges", []),
            key=lambda item: item.get("unlocked_at", ""),
            reverse=True,
        )
        badge_catalog = []
        for definition in BADGE_DEFINITIONS:
            unlocked_badge = next((badge for badge in unlocked_badges if badge.get("id") == definition["id"]), None)
            badge_catalog.append(
                {
                    "id": definition["id"],
                    "name": definition["name"],
                    "description": definition["description"],
                    "icon": definition["icon"],
                    "unlocked": definition["id"] in unlocked_badge_ids,
                    "unlocked_at": unlocked_badge.get("unlocked_at") if unlocked_badge else None,
                }
            )

        daily_goals = []
        for stat_name, target in DAILY_GOAL_TARGETS.items():
            current = _safe_int(today_progress.get(stat_name, 0))
            daily_goals.append(
                {
                    "id": stat_name,
                    "label": self._daily_goal_label(stat_name),
                    "current": current,
                    "target": target,
                    "completed": current >= target,
                    "progress_percent": round(min(100, (current / target) * 100), 2),
                }
            )

        stats = state["stats"]
        stats_payload = {
            "chapters_read": _safe_int(user_doc.get("chapters_read", 0)),
            "chapters_completed": _safe_int(stats.get("chapters_completed", 0)),
            "quizzes_attempted": _safe_int(user_doc.get("quizzes_attempted", stats.get("quizzes_attempted", 0))),
            "quizzes_completed": _safe_int(user_doc.get("quizzes_completed", 0)),
            "quizzes_perfect": _safe_int(stats.get("quizzes_perfect", 0)),
            "flashcards_correct": _safe_int(stats.get("flashcards_correct", 0)),
            "flashcard_levels_completed": _safe_int(stats.get("flashcard_levels_completed", 0)),
            "videos_completed": _safe_int(stats.get("videos_completed", 0)),
            "video_checkpoints_answered": _safe_int(stats.get("video_checkpoints_answered", 0)),
            "chat_questions_answered": _safe_int(stats.get("chat_questions_answered", 0)),
            "fsm_challenges_completed": _safe_int(stats.get("fsm_challenges_completed", 0)),
            "regex_challenges_completed": _safe_int(stats.get("regex_challenges_completed", 0)),
        }
        ring_metrics = self._build_ring_metrics(stats_payload)
        trend_payload = self._build_trend_payload(state)
        distribution_payload = self._build_distribution_payload(stats_payload)

        strengths = []
        if stats_payload["flashcards_correct"] >= 10:
            strengths.append("Flashcard recall is building steadily.")
        if stats_payload["videos_completed"] >= 3:
            strengths.append("Video-guided study is becoming a consistent habit.")
        if stats_payload["quizzes_perfect"] >= 1:
            strengths.append("You have already demonstrated perfect quiz performance.")
        if not strengths:
            strengths.append("You are early in the progression path. Keep completing lessons and practice tasks.")

        weak_spots = []
        if stats_payload["chapters_read"] > 0 and stats_payload["quizzes_attempted"] == 0:
            weak_spots.append("You are reading chapters but have not converted that into quiz practice yet.")
        if stats_payload["flashcards_correct"] < 5:
            weak_spots.append("Adaptive recall practice is still light. More flashcards will strengthen retention.")
        if stats_payload["videos_completed"] == 0:
            weak_spots.append("Video checkpoints and summaries are still unused.")
        if not weak_spots:
            weak_spots.append("No obvious weak spots from the available activity data.")

        return {
            "overview": {
                "total_xp": _safe_int(state["xp_total"]),
                "current_level": _safe_int(state["level"], 1),
                "current_streak": _safe_int(state["streak_days"]),
                "badge_count": len(unlocked_badges),
            },
            "xp_progress": xp_details,
            "daily_goals": daily_goals,
            "badges": {
                "unlocked": unlocked_badges,
                "catalog": badge_catalog,
            },
            "stats": stats_payload,
            "ring_metrics": ring_metrics,
            "trend_chart": trend_payload,
            "distribution_chart": distribution_payload,
            "recent_activity": state.get("activity_log", [])[:8],
            "header_summary": {
                "xp_total": _safe_int(state["xp_total"]),
                "level": _safe_int(state["level"], 1),
                "streak_days": _safe_int(state["streak_days"]),
            },
            "concept_snapshot": {
                "strengths": strengths[:3],
                "weak_spots": weak_spots[:3],
            },
        }

    def challenge_key(self, prefix: str, payload: str) -> str:
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}:{digest}:completed"

    def _apply_event_stats(
        self,
        state: dict[str, Any],
        event: RewardEvent,
        *,
        today_str: str,
    ) -> None:
        event_name = event.event_name
        if event_name == "chapter_completed":
            self.increment_stat(state, "chapters_completed", today_str=today_str)
        elif event_name == "quiz_attempted":
            self.increment_stat(state, "quizzes_attempted", today_str=today_str)
        elif event_name == "quiz_perfect":
            self.increment_stat(state, "quizzes_perfect", today_str=today_str)
        elif event_name == "flashcard_correct":
            self.increment_stat(state, "flashcards_correct", today_str=today_str)
        elif event_name == "flashcard_level_completed":
            self.increment_stat(state, "flashcard_levels_completed", today_str=today_str)
        elif event_name == "flashcard_path_completed":
            self.increment_stat(state, "flashcard_paths_completed", today_str=today_str)
        elif event_name == "video_checkpoint_answered":
            self.increment_stat(state, "video_checkpoints_answered", today_str=today_str)
        elif event_name == "video_completed":
            self.increment_stat(state, "videos_completed", today_str=today_str)
        elif event_name == "chat_question_answered":
            self.increment_stat(state, "chat_questions_answered", today_str=today_str)
        elif event_name == "fsm_practice_completed":
            self.increment_stat(state, "fsm_challenges_completed", today_str=today_str)
        elif event_name == "regex_practice_completed":
            self.increment_stat(state, "regex_challenges_completed", today_str=today_str)

    def _event_feedback_detail(self, event_name: str, metadata: dict[str, Any]) -> str:
        if event_name == "chapter_completed":
            return f"Completed {metadata.get('chapter_title', metadata.get('chapter_id', 'a chapter'))}."
        if event_name == "quiz_attempted":
            return f"Submitted quiz {metadata.get('chapter_id', '')}."
        if event_name == "quiz_perfect":
            return "Perfect quiz score achieved."
        if event_name == "flashcard_correct":
            return "Correct adaptive flashcard answer."
        if event_name == "flashcard_level_completed":
            return f"Completed the {metadata.get('level', 'current')} flashcard level."
        if event_name == "video_checkpoint_answered":
            return "Answered a video checkpoint."
        if event_name == "video_completed":
            return f"Completed video {metadata.get('video_title', metadata.get('video_id', 'study session'))}."
        if event_name == "video_summary_opened":
            return "Opened the post-video study summary."
        if event_name == "chat_question_answered":
            return "Answered a tutor-generated practice question."
        if event_name == "fsm_practice_completed":
            return "Solved an FSM practice challenge."
        if event_name == "regex_practice_completed":
            return "Solved a regex practice challenge."
        return "Progress recorded."

    def _build_activity_entry(self, event_name: str, metadata: dict[str, Any]) -> dict[str, Any]:
        if event_name == "chapter_completed":
            label = f"Completed Chapter {metadata.get('chapter_id', '')}".strip()
            entry_type = "chapter_completed"
        elif event_name == "quiz_attempted":
            label = f"Submitted Quiz {metadata.get('chapter_id', '')}".strip()
            entry_type = "quiz_attempted"
        elif event_name == "quiz_perfect":
            label = f"Perfect score on Chapter {metadata.get('chapter_id', '')} quiz".strip()
            entry_type = "quiz_perfect"
        elif event_name == "flashcard_correct":
            label = f"Answered flashcard on {metadata.get('concept', 'core concept')}".strip()
            entry_type = "flashcard_correct"
        elif event_name == "flashcard_level_completed":
            label = f"Completed {str(metadata.get('level', 'current')).capitalize()} flashcard level"
            entry_type = "flashcard_level_completed"
        elif event_name == "flashcard_path_completed":
            label = f"Completed full flashcard path for {metadata.get('entity_title', 'a study unit')}"
            entry_type = "flashcard_path_completed"
        elif event_name == "video_checkpoint_answered":
            label = f"Completed checkpoint in {metadata.get('video_title', metadata.get('video_id', 'video study'))}"
            entry_type = "video_checkpoint_answered"
        elif event_name == "video_completed":
            label = f"Finished video on {metadata.get('video_title', metadata.get('video_id', 'automata theory'))}"
            entry_type = "video_completed"
        elif event_name == "video_summary_opened":
            label = f"Opened summary for {metadata.get('video_title', metadata.get('video_id', 'video study'))}"
            entry_type = "video_summary_opened"
        elif event_name == "chat_question_answered":
            label = f"Answered tutor question on {metadata.get('topic', 'automata theory')}"
            entry_type = "chat_question_answered"
        elif event_name == "fsm_practice_completed":
            label = "Completed FSM challenge"
            entry_type = "fsm_practice_completed"
        elif event_name == "regex_practice_completed":
            label = "Completed regex challenge"
            entry_type = "regex_practice_completed"
        else:
            label = "Progress updated"
            entry_type = event_name

        return {
            "type": entry_type,
            "label": label,
            "timestamp": _timestamp(),
            "meta": metadata,
        }

    def _add_activity_entry(self, state: dict[str, Any], entry: dict[str, Any]) -> None:
        if not entry:
            return
        state.setdefault("activity_log", [])
        state["activity_log"].insert(0, entry)
        state["activity_log"] = _trim_list(state["activity_log"], limit=ACTIVITY_LOG_LIMIT)

    def _daily_goal_label(self, stat_name: str) -> str:
        labels = {
            "chapters_completed": "Read 1 chapter",
            "quizzes_attempted": "Finish 1 quiz",
            "flashcards_correct": "Answer 5 flashcards",
            "video_checkpoints_answered": "Watch 1 video checkpoint",
        }
        return labels.get(stat_name, stat_name.replace("_", " ").title())

    def _build_ring_metrics(self, stats_payload: dict[str, int]) -> list[dict[str, Any]]:
        chapter_total = self._chapter_count()
        video_total = self._video_count()
        flashcard_total = max(4, chapter_total * 4)
        quiz_total = chapter_total

        metric_specs = [
            {
                "id": "chapters",
                "label": "Chapter Progress",
                "current": stats_payload["chapters_read"],
                "total": chapter_total,
                "color_start": "#4fc3ff",
                "color_end": "#138de2",
                "detail": f"{stats_payload['chapters_completed']} counted toward progression",
            },
            {
                "id": "quizzes",
                "label": "Quiz Progress",
                "current": stats_payload["quizzes_completed"],
                "total": quiz_total,
                "color_start": "#8ae27a",
                "color_end": "#2dbf66",
                "detail": f"{stats_payload['quizzes_perfect']} perfect quiz milestones",
            },
            {
                "id": "flashcards",
                "label": "Flashcard Progress",
                "current": stats_payload["flashcard_levels_completed"],
                "total": flashcard_total,
                "color_start": "#ffcf70",
                "color_end": "#ff8f45",
                "detail": f"{stats_payload['flashcards_correct']} correct answers logged",
            },
            {
                "id": "videos",
                "label": "Video Progress",
                "current": stats_payload["videos_completed"],
                "total": video_total,
                "color_start": "#a48cff",
                "color_end": "#5c74ff",
                "detail": f"{stats_payload['video_checkpoints_answered']} checkpoints answered",
            },
        ]

        metrics = []
        for item in metric_specs:
            total = max(1, _safe_int(item["total"], 1))
            current = min(total, max(0, _safe_int(item["current"], 0)))
            percent = round((current / total) * 100, 2)
            metrics.append(
                {
                    **item,
                    "current": current,
                    "total": total,
                    "percent": percent,
                }
            )
        return metrics

    def _build_trend_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        daily_progress = state.get("daily_progress", {})
        activity_log = state.get("activity_log", [])
        ordered_days = sorted(daily_progress.keys())[-7:]
        if not ordered_days:
            ordered_days = [_today_str()]

        daily_xp: dict[str, int] = {day: 0 for day in ordered_days}
        daily_activity_counts: dict[str, int] = {
            day: sum(_safe_int(count) for count in (daily_progress.get(day) or {}).values()) for day in ordered_days
        }

        for entry in activity_log:
            if not isinstance(entry, dict):
                continue
            timestamp = str(entry.get("timestamp", ""))
            day = timestamp[:10]
            if day not in daily_xp:
                continue
            daily_xp[day] += self._xp_for_activity_type(str(entry.get("type", "")))

        labels = [day[5:] for day in ordered_days]
        xp_values = [daily_xp.get(day, 0) for day in ordered_days]
        activity_values = [daily_activity_counts.get(day, 0) for day in ordered_days]
        max_xp = max([1, *xp_values])
        max_activity = max([1, *activity_values])

        return {
            "labels": labels,
            "xp_values": xp_values,
            "activity_values": activity_values,
            "xp_points": [
                {"label": labels[index], "value": value, "height_percent": round((value / max_xp) * 100, 2)}
                for index, value in enumerate(xp_values)
            ],
            "activity_points": [
                {"label": labels[index], "value": value, "height_percent": round((value / max_activity) * 100, 2)}
                for index, value in enumerate(activity_values)
            ],
        }

    def _build_distribution_payload(self, stats_payload: dict[str, int]) -> list[dict[str, Any]]:
        items = [
            {
                "label": "Quizzes",
                "value": stats_payload["quizzes_attempted"],
                "color": "#36b37e",
            },
            {
                "label": "Videos",
                "value": stats_payload["videos_completed"] + stats_payload["video_checkpoints_answered"],
                "color": "#5c74ff",
            },
            {
                "label": "Flashcards",
                "value": stats_payload["flashcards_correct"] + stats_payload["flashcard_levels_completed"],
                "color": "#ff9f43",
            },
            {
                "label": "Practice",
                "value": stats_payload["fsm_challenges_completed"] + stats_payload["regex_challenges_completed"],
                "color": "#8e72ff",
            },
        ]

        total = max(1, sum(item["value"] for item in items))
        for item in items:
            item["percent"] = round((item["value"] / total) * 100, 2)
        return items

    def _chapter_count(self) -> int:
        try:
            return max(1, len(list(self.db.collection("chapters").stream())))
        except Exception:
            return TOTAL_CHAPTER_COUNT

    def _video_count(self) -> int:
        try:
            count = len(list(self.db.collection("videos").stream()))
            return max(1, count) if count else DEFAULT_VIDEO_TARGET
        except Exception:
            return DEFAULT_VIDEO_TARGET

    def _xp_for_activity_type(self, activity_type: str) -> int:
        lookup = {
            "chapter_completed": XP_RULES["chapter_completed"],
            "quiz_attempted": XP_RULES["quiz_attempted"],
            "quiz_perfect": XP_RULES["quiz_perfect"],
            "flashcard_correct": XP_RULES["flashcard_correct"],
            "flashcard_level_completed": XP_RULES["flashcard_level_completed"],
            "flashcard_path_completed": 0,
            "video_checkpoint_answered": XP_RULES["video_checkpoint_answered"],
            "video_completed": XP_RULES["video_completed"],
            "video_summary_opened": XP_RULES["video_summary_opened"],
            "chat_question_answered": XP_RULES["chat_question_answered"],
            "fsm_practice_completed": XP_RULES["fsm_practice_completed"],
            "regex_practice_completed": XP_RULES["regex_practice_completed"],
            "streak_bonus": XP_RULES["daily_streak_bonus"],
        }
        return lookup.get(activity_type, 0)
