"""Diagnoser agent for evaluating learner answers."""

from __future__ import annotations

import json
import logging
import os
import re

import google.generativeai as genai

from models.chat_types import DiagnoserResult, QuestionObject
from services.prompt_builder import build_diagnoser_prompt
from services.retrieval_service import RetrievalService


def _extract_json_payload(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in diagnoser response")
    return json.loads(match.group(0))


class DiagnoserAgent:
    """Evaluates user answers and provides corrective feedback."""

    def __init__(self, retrieval_service: RetrievalService) -> None:
        self.retrieval_service = retrieval_service
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
        self.model = genai.GenerativeModel(os.environ.get("CHAT_MODEL_NAME", "gemini-2.5-flash-lite"))

    def diagnose(self, question: QuestionObject, user_answer: str) -> DiagnoserResult:
        if question.type == "mcq":
            result = self._diagnose_mcq(question, user_answer)
        else:
            result = self._diagnose_short_answer(question, user_answer)
        logging.info(
            "Diagnosis complete: question_id=%s is_correct=%s score=%.2f misconception=%s",
            question.question_id,
            result.is_correct,
            result.score,
            result.misconception,
        )
        return result

    def _diagnose_mcq(self, question: QuestionObject, user_answer: str) -> DiagnoserResult:
        normalized_answer = self._normalize_mcq_answer(user_answer, question)
        correct_answer = self._normalize_text(question.correct_answer)
        is_correct = normalized_answer == correct_answer
        score = 1.0 if is_correct else 0.0
        feedback = (
            f"Correct. {question.explanation}"
            if is_correct
            else (
                f"Not quite. The correct answer is: {question.correct_answer}. "
                f"{question.explanation or 'Review the defining property carefully.'}"
            )
        )
        misconception = None if is_correct else "misread_definition_or_option"
        return DiagnoserResult(
            agent="diagnoser",
            is_correct=is_correct,
            score=score,
            feedback=feedback,
            misconception=misconception,
            concepts=question.concept_tags,
            next_recommendation="Try another question or ask for an explanation of the concept.",
        )

    def _diagnose_short_answer(self, question: QuestionObject, user_answer: str) -> DiagnoserResult:
        retrieval_query = " ".join(question.concept_tags) or question.prompt
        context = self.retrieval_service.retrieve_text(retrieval_query, k=3)
        prompt = build_diagnoser_prompt(question, user_answer, context or "Limited retrieval context available.")

        try:
            response = self.model.generate_content(prompt)
            payload = _extract_json_payload(response.text)
            score = float(payload.get("score", 0.0))
            is_correct = bool(payload.get("is_correct")) if score >= 0.75 else False
            return DiagnoserResult(
                agent="diagnoser",
                is_correct=is_correct,
                score=max(0.0, min(score, 1.0)),
                feedback=str(payload.get("feedback", "I could not fully evaluate that answer.")),
                misconception=payload.get("misconception"),
                concepts=list(payload.get("concepts", question.concept_tags)),
                next_recommendation=payload.get(
                    "next_recommendation",
                    "If you want, I can explain the concept again or ask a follow-up question.",
                ),
            )
        except Exception as exc:
            logging.error("Diagnoser short-answer evaluation failed: %s", exc)
            return DiagnoserResult(
                agent="diagnoser",
                is_correct=False,
                score=0.0,
                feedback=(
                    "I could not confidently evaluate that short answer. "
                    "Tentatively, compare your answer against the key idea: "
                    f"{question.correct_answer or question.explanation}"
                ),
                misconception="uncertain_short_answer",
                concepts=question.concept_tags,
                next_recommendation="Try a more precise answer or ask me to explain the concept again.",
            )

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    def _normalize_mcq_answer(self, user_answer: str, question: QuestionObject) -> str:
        answer = self._normalize_text(user_answer)
        if answer in {"a", "b", "c", "d"} and question.options:
            index = ord(answer.upper()) - ord("A")
            if 0 <= index < len(question.options):
                return self._normalize_text(question.options[index])
        return answer
