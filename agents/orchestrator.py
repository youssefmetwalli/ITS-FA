"""Rule-based orchestrator for multi-agent tutoring chat."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from agents.diagnoser_agent import DiagnoserAgent
from agents.examiner_agent import ExaminerAgent
from agents.explainer_agent import ExplainerAgent
from models.chat_types import ChatState, DiagnoserResult, ExaminerResult, ExplainerResult
from services.chat_state_service import ChatStateService


@dataclass
class RouteDecision:
    agent_name: str
    reason: str


class ChatOrchestrator:
    """Routes chat requests to the appropriate tutoring agent."""

    def __init__(
        self,
        explainer_agent: ExplainerAgent,
        examiner_agent: ExaminerAgent,
        diagnoser_agent: DiagnoserAgent,
        chat_state_service: ChatStateService,
    ) -> None:
        self.explainer_agent = explainer_agent
        self.examiner_agent = examiner_agent
        self.diagnoser_agent = diagnoser_agent
        self.chat_state_service = chat_state_service

    def handle_message(self, message: str, state: ChatState) -> tuple[dict[str, Any], ChatState]:
        decision = self.route_message(message, state)
        logging.info("Chat orchestrator selected agent=%s reason=%s", decision.agent_name, decision.reason)

        try:
            if decision.agent_name == "diagnoser" and state.active_question is not None:
                result = self.diagnoser_agent.diagnose(state.active_question, message)
                state = self.chat_state_service.set_recent_agent(state, "diagnoser")
                if state.pending_questions:
                    state = self.chat_state_service.advance_question_queue(state)
                else:
                    state = self.chat_state_service.clear_active_question(state)
                return self._build_payload(result, state, routing_reason=decision.reason), state

            if decision.agent_name == "examiner":
                result = self.examiner_agent.generate_questions(message)
                state = self.chat_state_service.set_recent_agent(state, "examiner")
                active_question = result.questions[0] if result.questions else None
                pending_questions = result.questions[1:] if len(result.questions) > 1 else []
                state = self.chat_state_service.set_active_question(state, active_question, pending_questions)
                return self._build_payload(result, state, routing_reason=decision.reason), state

            result = self.explainer_agent.explain(message)
            state = self.chat_state_service.set_recent_agent(state, "explainer")
            state.concept_tags = result.concepts_covered
            return self._build_payload(result, state, routing_reason=decision.reason), state
        except Exception as exc:
            logging.error("Orchestrator execution failed: %s", exc)
            fallback = self.explainer_agent.explain(message)
            state = self.chat_state_service.set_recent_agent(state, "explainer")
            return self._build_payload(fallback, state, routing_reason="fallback_to_explainer"), state

    def route_message(self, message: str, state: ChatState) -> RouteDecision:
        lowered = message.strip().lower()

        if self._is_examiner_request(lowered):
            return RouteDecision("examiner", "examiner_keywords")
        if self._is_explainer_request(lowered):
            return RouteDecision("explainer", "explainer_keywords")
        if state.active_question and self._looks_like_answer_attempt(lowered):
            return RouteDecision("diagnoser", "active_question_answer_attempt")
        return RouteDecision("explainer", "default_explainer")

    @staticmethod
    def _is_explainer_request(message: str) -> bool:
        patterns = [
            r"\bexplain\b",
            r"\bteach me\b",
            r"\bwhat is\b",
            r"\bclarify\b",
            r"\bhow does\b",
            r"\bhelp me understand\b",
        ]
        return any(re.search(pattern, message) for pattern in patterns)

    @staticmethod
    def _is_examiner_request(message: str) -> bool:
        patterns = [
            r"\bquiz me\b",
            r"\bask me(?: [a-z]+){0,4} question\b",
            r"\btest me\b",
            r"\bgive me \d+ questions?\b",
            r"\bgive me(?: [a-z]+){0,4} question\b",
            r"\bpractice question\b",
            r"\bquestion on\b",
            r"\bquestion about\b",
            r"\bmcq\b",
            r"\bshort[- ]answer\b",
        ]
        return any(re.search(pattern, message) for pattern in patterns)

    @staticmethod
    def _looks_like_answer_attempt(message: str) -> bool:
        explicit_commands = [
            r"\bexplain\b",
            r"\bteach me\b",
            r"\bquiz me\b",
            r"\bask me\b",
            r"\btest me\b",
            r"\bclarify\b",
            r"\bgive me\b",
            r"\bwhat is\b",
            r"\bhow does\b",
            r"\bhelp me understand\b",
        ]
        if any(re.search(pattern, message) for pattern in explicit_commands):
            return False
        answer_patterns = [
            r"^[abcd]$",
            r"^option\s+[abcd]$",
            r"^(the\s+)?answer\s+is\s+[abcd]\b",
            r"^i think (the answer is )?[abcd]\b",
            r"^my answer is\b",
            r"^it is\b",
            r"^because\b",
        ]
        if any(re.search(pattern, message) for pattern in answer_patterns):
            return True
        return len(message.split()) <= 12 and not message.endswith("?")

    @staticmethod
    def _build_payload(result: Any, state: ChatState, routing_reason: str) -> dict[str, Any]:
        if isinstance(result, ExplainerResult):
            response_text = result.response_text
            concepts = result.concepts_covered
            suggested_next_action = result.suggested_next_action
            question_metadata = None
            extra = result.to_dict()
        elif isinstance(result, ExaminerResult):
            response_text = result.response_text
            concepts = result.questions[0].concept_tags if result.questions else []
            suggested_next_action = result.suggested_next_action
            active_question = state.active_question.to_dict() if state.active_question else None
            question_metadata = active_question
            extra = result.to_dict()
        else:
            response_text = result.feedback
            concepts = result.concepts
            suggested_next_action = result.next_recommendation
            question_metadata = state.active_question.to_dict() if state.active_question else None
            extra = result.to_dict()

        return {
            "message": response_text,
            "response_text": response_text,
            "agent_used": result.agent,
            "has_active_question": state.active_question is not None,
            "question_metadata": question_metadata,
            "concepts_touched": concepts,
            "suggested_next_action": suggested_next_action,
            "routing_reason": routing_reason,
            "agent_payload": extra,
        }
