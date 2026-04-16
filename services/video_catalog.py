"""Video catalog helpers for section-based video learning."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_SECTION_VIDEOS: dict[str, list[dict[str, Any]]] = {
    "fsm_regular": [
        {
            "id": "fsm_video_01",
            "section_key": "fsm_regular",
            "title": "Finite State Machines and Regular Languages",
            "url": "",
            "description": "TODO: Replace this placeholder with a finite state machine section video.",
            "youtube_video_id": "",
            "transcript_text": "",
            "generated_summary": None,
            "checkpoint_questions": [
                {
                    "timestamp_seconds": 90,
                    "question": "What does a DFA use to move between states?",
                    "options": [
                        "Only epsilon moves",
                        "Input symbols and transition rules",
                        "A stack and grammar rules",
                        "A tape head and blank symbols",
                    ],
                    "correct_answer": "Input symbols and transition rules",
                    "explanation": "A DFA changes state according to the current input symbol and its transition function.",
                }
            ],
        }
    ],
    "cfl_pda": [
        {
            "id": "cfl_video_01",
            "section_key": "cfl_pda",
            "title": "Context-Free Grammars and Pushdown Automata",
            "url": "",
            "description": "TODO: Replace this placeholder with a CFG/PDA section video.",
            "youtube_video_id": "",
            "transcript_text": "",
            "generated_summary": None,
            "checkpoint_questions": [
                {
                    "timestamp_seconds": 120,
                    "question": "What extra memory structure gives a PDA more power than a DFA?",
                    "options": [
                        "A queue",
                        "A stack",
                        "A second input tape",
                        "An epsilon graph",
                    ],
                    "correct_answer": "A stack",
                    "explanation": "Pushdown automata use a stack, which lets them recognize many context-free languages.",
                }
            ],
        }
    ],
    "tm_undecidability": [
        {
            "id": "tm_video_01",
            "section_key": "tm_undecidability",
            "title": "Turing Machines and Undecidability",
            "url": "",
            "description": "TODO: Replace this placeholder with a Turing machines section video.",
            "youtube_video_id": "",
            "transcript_text": "",
            "generated_summary": None,
            "checkpoint_questions": [
                {
                    "timestamp_seconds": 150,
                    "question": "Why is the Turing machine model important?",
                    "options": [
                        "It proves every problem is decidable",
                        "It captures a broad notion of algorithmic computation",
                        "It eliminates the need for proofs",
                        "It only works for regular languages",
                    ],
                    "correct_answer": "It captures a broad notion of algorithmic computation",
                    "explanation": "Turing machines are a foundational model for reasoning about what computation can do in principle.",
                }
            ],
        }
    ],
    "complexity": [
        {
            "id": "complexity_video_01",
            "section_key": "complexity",
            "title": "Complexity Classes and Efficient Computation",
            "url": "",
            "description": "TODO: Replace this placeholder with a complexity section video.",
            "youtube_video_id": "",
            "transcript_text": "",
            "generated_summary": None,
            "checkpoint_questions": [
                {
                    "timestamp_seconds": 95,
                    "question": "What does complexity theory mainly compare?",
                    "options": [
                        "Only grammar derivations",
                        "Resource usage such as time and space",
                        "Only the alphabet size",
                        "Only the number of final states",
                    ],
                    "correct_answer": "Resource usage such as time and space",
                    "explanation": "Complexity theory studies how much time or space is needed to solve problems.",
                }
            ],
        }
    ],
    "logic_proofs": [
        {
            "id": "logic_video_01",
            "section_key": "logic_proofs",
            "title": "Logic, Proofs, and Formal Reasoning",
            "url": "",
            "description": "TODO: Replace this placeholder with a logic and proofs section video.",
            "youtube_video_id": "",
            "transcript_text": "",
            "generated_summary": None,
            "checkpoint_questions": [
                {
                    "timestamp_seconds": 80,
                    "question": "Why are proof techniques important in theory?",
                    "options": [
                        "They replace definitions",
                        "They justify claims rigorously",
                        "They remove the need for examples",
                        "They only matter in programming courses",
                    ],
                    "correct_answer": "They justify claims rigorously",
                    "explanation": "Formal proofs are what make theoretical claims precise and defensible.",
                }
            ],
        }
    ],
}


def _normalized_checkpoint_ids(checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, checkpoint in enumerate(checkpoints):
        if not isinstance(checkpoint, dict):
            continue
        item = dict(checkpoint)
        item["checkpoint_id"] = str(item.get("checkpoint_id") or f"checkpoint_{index}")
        normalized.append(item)
    return normalized


def normalize_video_record(record: dict[str, Any], fallback_id: str | None = None) -> dict[str, Any]:
    """Normalize a video record from Firestore or local config."""
    normalized = dict(record)
    normalized["id"] = str(normalized.get("id") or fallback_id or "").strip()
    normalized["section_key"] = str(normalized.get("section_key", "")).strip()
    normalized["title"] = str(normalized.get("title", "")).strip()
    normalized["url"] = str(normalized.get("url", "")).strip()
    normalized["description"] = str(normalized.get("description", "")).strip()
    normalized["youtube_video_id"] = str(normalized.get("youtube_video_id", "")).strip()
    normalized["transcript_text"] = str(normalized.get("transcript_text", "")).strip()
    normalized["generated_summary"] = normalized.get("generated_summary")
    normalized["checkpoint_questions"] = _normalized_checkpoint_ids(
        list(normalized.get("checkpoint_questions", []))
    )
    return normalized


def list_section_videos(db: Any, section_key: str) -> list[dict[str, Any]]:
    """Return section videos from Firestore, falling back to local placeholders."""
    firestore_videos: list[dict[str, Any]] = []
    try:
        docs = db.collection("videos").where("section_key", "==", section_key).stream()
        for doc in docs:
            data = doc.to_dict() or {}
            firestore_videos.append(normalize_video_record(data, fallback_id=doc.id))
    except Exception:
        firestore_videos = []

    if firestore_videos:
        firestore_videos.sort(key=lambda item: (item.get("title", ""), item.get("id", "")))
        return firestore_videos

    defaults = DEFAULT_SECTION_VIDEOS.get(section_key, [])
    return [normalize_video_record(deepcopy(item), fallback_id=item.get("id")) for item in defaults]


def get_video_by_id(db: Any, video_id: str) -> dict[str, Any] | None:
    """Load one video by id from Firestore or the local fallback catalog."""
    try:
        doc = db.collection("videos").document(video_id).get()
        if doc.exists:
            return normalize_video_record(doc.to_dict() or {}, fallback_id=doc.id)
    except Exception:
        pass

    for videos in DEFAULT_SECTION_VIDEOS.values():
        for video in videos:
            if str(video.get("id")) == video_id:
                return normalize_video_record(deepcopy(video), fallback_id=video_id)
    return None


def build_section_video_meta(db: Any, section_configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build section metadata for rendering course page video buttons."""
    meta: dict[str, dict[str, Any]] = {}
    for config in section_configs:
        videos = list_section_videos(db, config["key"])
        meta[config["key"]] = {
            "available": bool(videos),
            "count": len(videos),
        }
    return meta
