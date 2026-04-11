"""Structured chat types for the tutoring agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


AgentName = Literal["explainer", "examiner", "diagnoser"]
QuestionKind = Literal["mcq", "short_answer"]
DifficultyLevel = Literal["easy", "medium", "hard"]


@dataclass
class QuestionObject:
    """A structured tutoring question."""

    question_id: str
    type: QuestionKind
    concept_tags: list[str]
    difficulty: DifficultyLevel
    prompt: str
    options: list[str] = field(default_factory=list)
    correct_answer: str = ""
    explanation: str = ""
    hint: str = ""
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "QuestionObject | None":
        if not data:
            return None
        return cls(
            question_id=data.get("question_id", ""),
            type=data.get("type", "short_answer"),
            concept_tags=list(data.get("concept_tags", [])),
            difficulty=data.get("difficulty", "medium"),
            prompt=data.get("prompt", ""),
            options=list(data.get("options", [])),
            correct_answer=data.get("correct_answer", ""),
            explanation=data.get("explanation", ""),
            hint=data.get("hint", ""),
            timestamp=data.get("timestamp"),
        )


@dataclass
class ChatState:
    """Session-backed chat state with an active tutoring task."""

    active_question: QuestionObject | None = None
    pending_questions: list[QuestionObject] = field(default_factory=list)
    concept_tags: list[str] = field(default_factory=list)
    difficulty: str | None = None
    timestamp: str | None = None
    recent_agent_used: AgentName | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_question": self.active_question.to_dict() if self.active_question else None,
            "pending_questions": [question.to_dict() for question in self.pending_questions],
            "concept_tags": self.concept_tags,
            "difficulty": self.difficulty,
            "timestamp": self.timestamp,
            "recent_agent_used": self.recent_agent_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ChatState":
        if not data:
            return cls()
        return cls(
            active_question=QuestionObject.from_dict(data.get("active_question")),
            pending_questions=[
                question
                for question in (
                    QuestionObject.from_dict(item) for item in data.get("pending_questions", [])
                )
                if question is not None
            ],
            concept_tags=list(data.get("concept_tags", [])),
            difficulty=data.get("difficulty"),
            timestamp=data.get("timestamp"),
            recent_agent_used=data.get("recent_agent_used"),
        )


@dataclass
class ExplainerResult:
    """Structured output from the explainer agent."""

    agent: AgentName
    response_text: str
    concepts_covered: list[str]
    estimated_level: str
    suggested_next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExaminerResult:
    """Structured output from the examiner agent."""

    agent: AgentName
    response_text: str
    questions: list[QuestionObject]
    suggested_next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["questions"] = [question.to_dict() for question in self.questions]
        return payload


@dataclass
class DiagnoserResult:
    """Structured output from the diagnoser agent."""

    agent: AgentName
    is_correct: bool | None
    score: float
    feedback: str
    misconception: str | None
    concepts: list[str]
    next_recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
