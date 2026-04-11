"""Explainer agent for grounded automata tutoring."""

from __future__ import annotations

import json
import logging
import os
import re

import google.generativeai as genai

from models.chat_types import ExplainerResult
from services.prompt_builder import build_explainer_prompt
from services.retrieval_service import RetrievalService


def _extract_json_payload(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))


class ExplainerAgent:
    """Grounded explainer over retrieved automata content."""

    def __init__(self, retrieval_service: RetrievalService) -> None:
        self.retrieval_service = retrieval_service
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
        self.model = genai.GenerativeModel(os.environ.get("CHAT_MODEL_NAME", "gemini-2.5-flash-lite"))

    def explain(self, message: str) -> ExplainerResult:
        depth_hint = self._estimate_depth(message)
        context = self.retrieval_service.retrieve_text(message, k=4)
        if not context:
            logging.info("Explainer proceeding with limited retrieval context.")

        prompt = build_explainer_prompt(message, context or "Limited retrieval context available.", depth_hint)

        try:
            response = self.model.generate_content(prompt)
            payload = _extract_json_payload(response.text)
            return ExplainerResult(
                agent="explainer",
                response_text=payload.get(
                    "response_text",
                    "I can explain automata theory concepts, but I could not format a full tutoring response this time.",
                ),
                concepts_covered=list(payload.get("concepts_covered", [])),
                estimated_level=str(payload.get("estimated_level", depth_hint)),
                suggested_next_action=payload.get("suggested_next_action"),
            )
        except Exception as exc:
            logging.error("Explainer agent failed: %s", exc)
            fallback_text = self._fallback_response(message, context)
            return ExplainerResult(
                agent="explainer",
                response_text=fallback_text,
                concepts_covered=self._extract_concepts(message),
                estimated_level=depth_hint,
                suggested_next_action="I can also quiz you on this topic if you want practice.",
            )

    @staticmethod
    def _estimate_depth(message: str) -> str:
        lowered = message.lower()
        if any(token in lowered for token in ["simply", "simple", "beginner", "intuitive", "basic"]):
            return "introductory"
        if any(token in lowered for token in ["rigorous", "formal", "proof", "advanced", "deep"]):
            return "advanced"
        return "intermediate"

    @staticmethod
    def _extract_concepts(message: str) -> list[str]:
        concepts = re.findall(r"\b(dfa|nfa|regex|regular expression|pda|cfg|grammar|turing machine|minimization)\b", message.lower())
        return list(dict.fromkeys(concepts))

    def _fallback_response(self, message: str, context: str) -> str:
        if context:
            preview = context[:700].strip()
            return (
                f"I can explain this within automata theory, but the structured explainer failed. "
                f"Here is a grounded summary based on the retrieved material:\n\n{preview}"
            )
        return (
            f"I can help with automata theory, but I had limited retrieval support for '{message}'. "
            "Ask about a specific concept like DFA minimization, regular languages, or context-free grammars."
        )
