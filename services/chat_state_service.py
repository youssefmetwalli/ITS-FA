"""Session-backed chat state management with a clean persistence seam."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import session

from models.chat_types import ChatState, QuestionObject


class ChatStateService:
    """Stores and retrieves tutoring chat state from the Flask session."""

    SESSION_KEY = "chat_state"

    def load_state(self) -> ChatState:
        return ChatState.from_dict(session.get(self.SESSION_KEY))

    def save_state(self, state: ChatState) -> None:
        session[self.SESSION_KEY] = state.to_dict()
        session.modified = True

    def set_recent_agent(self, state: ChatState, agent_name: str) -> ChatState:
        state.recent_agent_used = agent_name  # type: ignore[assignment]
        state.timestamp = self._now()
        return state

    def set_active_question(
        self,
        state: ChatState,
        question: QuestionObject | None,
        pending_questions: list[QuestionObject] | None = None,
    ) -> ChatState:
        now = self._now()
        if question:
            question.timestamp = now
        state.active_question = question
        state.pending_questions = pending_questions or []
        if question:
            state.concept_tags = question.concept_tags
            state.difficulty = question.difficulty
        state.timestamp = now
        return state

    def advance_question_queue(self, state: ChatState) -> ChatState:
        next_question = state.pending_questions.pop(0) if state.pending_questions else None
        return self.set_active_question(state, next_question, state.pending_questions)

    def clear_active_question(self, state: ChatState) -> ChatState:
        state.active_question = None
        state.pending_questions = []
        state.timestamp = self._now()
        return state

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
