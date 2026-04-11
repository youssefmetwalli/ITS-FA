"""Examiner agent for generating structured automata questions."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid

import google.generativeai as genai

from models.chat_types import ExaminerResult, QuestionObject
from services.prompt_builder import build_examiner_prompt


def _extract_json_payload(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in examiner response")
    payload = match.group(0)
    payload = re.sub(r"^```json\s*", "", payload, flags=re.IGNORECASE)
    payload = re.sub(r"\s*```$", "", payload, flags=re.IGNORECASE)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        sanitized_payload = _sanitize_json_like_text(payload)
        try:
            return json.loads(sanitized_payload)
        except json.JSONDecodeError:
            logging.error("Examiner JSON parse failed. Original payload snippet: %s", payload[:500])
            raise exc


def _sanitize_json_like_text(payload: str) -> str:
    """Repair common LLM JSON issues without changing the response shape."""
    # Escape invalid backslashes such as \epsilon while preserving valid JSON escapes.
    payload = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", payload)
    # Remove trailing commas before closing braces/brackets.
    payload = re.sub(r",(\s*[}\]])", r"\1", payload)
    return payload


class ExaminerAgent:
    """Generates MCQ and short-answer questions inside chat."""

    def __init__(self) -> None:
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
        self.model = genai.GenerativeModel(os.environ.get("CHAT_MODEL_NAME", "gemini-2.5-flash-lite"))

    def generate_questions(self, message: str) -> ExaminerResult:
        topic = self._infer_topic(message)
        difficulty = self._infer_difficulty(message)
        question_count = self._infer_question_count(message)
        question_types = self._infer_question_types(message)

        prompt = build_examiner_prompt(topic, difficulty, question_count, question_types)
        try:
            response = self.model.generate_content(prompt)
            payload = _extract_json_payload(response.text)
            questions = [self._coerce_question(item, difficulty) for item in payload.get("questions", [])]
            questions = [question for question in questions if question is not None]
            if not questions:
                raise ValueError("Examiner returned no usable questions")
            response_text = payload.get("response_text", f"Here is a {difficulty} question on {topic}.")
            response_text = self._format_question_block(response_text, questions)
            return ExaminerResult(
                agent="examiner",
                response_text=response_text,
                questions=questions,
                suggested_next_action=payload.get("suggested_next_action", "Answer the active question in the chat."),
            )
        except Exception as exc:
            logging.error("Examiner agent failed: %s", exc)
            question = self._fallback_question(topic, difficulty, question_types[0])
            return ExaminerResult(
                agent="examiner",
                response_text=self._format_question_block(
                    f"Here is a {difficulty} practice question on {topic}.",
                    [question],
                ),
                questions=[question],
                suggested_next_action="Answer the question in the chat and I will evaluate it.",
            )

    @staticmethod
    def _infer_topic(message: str) -> str:
        lowered = message.lower()
        match = re.search(r"(?:on|about)\s+([a-z0-9 \-]+)", lowered)
        if match:
            topic = match.group(1).strip()
            return ExaminerAgent._normalize_topic(topic)
        if "regex" in lowered:
            return "regular expressions"
        if "dfa" in lowered:
            return "DFA"
        if "nfa" in lowered:
            return "NFA"
        return "automata theory"

    @staticmethod
    def _infer_difficulty(message: str) -> str:
        lowered = message.lower()
        if any(token in lowered for token in ("simple", "basic", "beginner", "intro")):
            return "easy"
        for difficulty in ("easy", "medium", "hard"):
            if difficulty in lowered:
                return difficulty
        return "medium"

    @staticmethod
    def _infer_question_count(message: str) -> int:
        match = re.search(r"\b([1-5])\b", message)
        if match:
            return max(1, min(5, int(match.group(1))))
        return 1

    @staticmethod
    def _infer_question_types(message: str) -> list[str]:
        lowered = message.lower()
        if "mcq" in lowered or "multiple choice" in lowered:
            return ["mcq"]
        if "short" in lowered or "conceptual" in lowered:
            return ["short_answer"]
        return ["mcq", "short_answer"]

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        topic = re.sub(r"^(on|about)\s+", "", topic.strip(), flags=re.IGNORECASE)
        topic = re.sub(r"\bquestion(s)?\b", "", topic, flags=re.IGNORECASE).strip(" -")
        lowered = topic.lower()
        if lowered == "dfa" or lowered == "dfas":
            return "DFA"
        if lowered == "nfa" or lowered == "nfas":
            return "NFA"
        if lowered == "cfg" or lowered == "cfgs":
            return "CFG"
        if lowered == "pda" or lowered == "pdas":
            return "PDA"
        return topic or "automata theory"

    @staticmethod
    def _format_question_block(intro_text: str, questions: list[QuestionObject]) -> str:
        lines = [intro_text.strip()]
        for index, question in enumerate(questions, start=1):
            lines.append("")
            lines.append(f"**Question {index} ({question.type}, {question.difficulty})**")
            lines.append(question.prompt)
            if question.options:
                for option_index, option in enumerate(question.options):
                    label = chr(ord("A") + option_index)
                    lines.append(f"{label}. {option}")
            if question.hint:
                lines.append(f"Hint: {question.hint}")
        return "\n".join(lines).strip()

    @staticmethod
    def _coerce_question(item: dict, default_difficulty: str) -> QuestionObject | None:
        prompt = ExaminerAgent._clean_generated_text(str(item.get("prompt", "")).strip())
        if not prompt:
            return None
        question_type = str(item.get("type", "short_answer")).strip().lower()
        options = ExaminerAgent._extract_options(item)
        if question_type == "mcq":
            if len(options) < 2:
                logging.warning("Examiner returned MCQ without usable options. Falling back to a safe MCQ scaffold.")
                fallback = ExaminerAgent._fallback_question(
                    topic=str(item.get("concept_tags", ["automata theory"])[0] if item.get("concept_tags") else "automata theory"),
                    difficulty=str(item.get("difficulty", default_difficulty)),
                    question_type="mcq",
                )
                if prompt:
                    fallback.prompt = prompt
                explanation = ExaminerAgent._clean_generated_text(str(item.get("explanation", "")).strip())
                hint = ExaminerAgent._clean_generated_text(str(item.get("hint", "")).strip())
                correct_answer = ExaminerAgent._clean_generated_text(str(item.get("correct_answer", "")).strip())
                if explanation:
                    fallback.explanation = explanation
                if hint:
                    fallback.hint = hint
                if correct_answer:
                    fallback.correct_answer = correct_answer
                fallback.concept_tags = list(item.get("concept_tags", fallback.concept_tags))
                fallback.difficulty = item.get("difficulty", default_difficulty)
                fallback.question_id = str(item.get("question_id") or uuid.uuid4().hex[:8])
                return fallback
            options = options[:4]
        else:
            options = []

        return QuestionObject(
            question_id=str(item.get("question_id") or uuid.uuid4().hex[:8]),
            type="mcq" if question_type == "mcq" else "short_answer",
            concept_tags=list(item.get("concept_tags", [])),
            difficulty=item.get("difficulty", default_difficulty),
            prompt=prompt,
            options=options,
            correct_answer=ExaminerAgent._clean_generated_text(str(item.get("correct_answer", "")).strip()),
            explanation=ExaminerAgent._clean_generated_text(str(item.get("explanation", "")).strip()),
            hint=ExaminerAgent._clean_generated_text(str(item.get("hint", "")).strip()),
        )

    @staticmethod
    def _extract_options(item: dict) -> list[str]:
        raw_options = item.get("options")
        if isinstance(raw_options, list):
            parsed = ExaminerAgent._normalize_option_list(raw_options)
            if parsed:
                return parsed
        if isinstance(raw_options, dict):
            parsed = ExaminerAgent._normalize_option_list([raw_options])
            if parsed:
                return parsed
        if isinstance(raw_options, str):
            parsed = ExaminerAgent._split_option_text(raw_options)
            if parsed:
                return parsed

        for alternate_key in ("choices", "answers", "answer_choices"):
            alternate_options = item.get(alternate_key)
            if isinstance(alternate_options, list):
                parsed = ExaminerAgent._normalize_option_list(alternate_options)
                if parsed:
                    return parsed
            if isinstance(alternate_options, dict):
                parsed = ExaminerAgent._normalize_option_list([alternate_options])
                if parsed:
                    return parsed
            if isinstance(alternate_options, str):
                parsed = ExaminerAgent._split_option_text(alternate_options)
                if parsed:
                    return parsed

        prompt_text = str(item.get("prompt", ""))
        prompt_options = ExaminerAgent._extract_options_from_prompt(prompt_text)
        return prompt_options

    @staticmethod
    def _split_option_text(raw_text: str) -> list[str]:
        parts = re.split(r"(?:\n|;\s*|\|\s*)", raw_text)
        cleaned = []
        for part in parts:
            option = re.sub(r"^[A-D][\).\s:-]+", "", part.strip())
            if option:
                cleaned.append(option)
        return cleaned

    @staticmethod
    def _normalize_option_list(raw_options: list) -> list[str]:
        cleaned: list[str] = []
        for option in raw_options:
            cleaned.extend(ExaminerAgent._normalize_single_option(option))

        return cleaned

    @staticmethod
    def _normalize_single_option(option: object) -> list[str]:
        if option is None:
            return []

        if isinstance(option, (list, tuple)):
            flattened: list[str] = []
            for item in option:
                flattened.extend(ExaminerAgent._normalize_single_option(item))
            return flattened

        if isinstance(option, dict):
            for key in ("text", "option", "value", "answer", "label"):
                value = option.get(key)
                if value:
                    return ExaminerAgent._normalize_single_option(value)
            return []

        option_text = str(option).strip()
        if not option_text:
            return []

        split_parts = ExaminerAgent._split_option_text(option_text)
        if len(split_parts) > 1:
            return [ExaminerAgent._clean_generated_text(part) for part in split_parts if part]

        comma_split = re.split(r"\s*,\s*(?=[A-D][\).:-]?\s*)", option_text)
        if len(comma_split) > 1:
            normalized_parts: list[str] = []
            for part in comma_split:
                normalized_parts.extend(ExaminerAgent._normalize_single_option(part))
            return normalized_parts

        return [ExaminerAgent._clean_generated_text(option_text)]

    @staticmethod
    def _extract_options_from_prompt(prompt_text: str) -> list[str]:
        matches = re.findall(r"(?:^|\n)\s*[A-D][\).:-]\s*(.+?)(?=(?:\n\s*[A-D][\).:-]\s*)|$)", prompt_text, flags=re.DOTALL)
        return [ExaminerAgent._clean_generated_text(match.strip()) for match in matches if match.strip()]

    @staticmethod
    def _clean_generated_text(text: str) -> str:
        if not text:
            return text

        replacements = {
            r"\Sigma": "Sigma",
            r"\delta": "delta",
            r"\epsilon": "epsilon",
            r"\lambda": "lambda",
            r"\to": "->",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)

        text = re.sub(r"\$([^$]+)\$", r"\1", text)
        text = text.replace("{", "").replace("}", "")
        text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _fallback_question(topic: str, difficulty: str, question_type: str) -> QuestionObject:
        if question_type == "mcq":
            return QuestionObject(
                question_id=uuid.uuid4().hex[:8],
                type="mcq",
                concept_tags=[topic],
                difficulty=difficulty,  # type: ignore[arg-type]
                prompt=f"Which statement best describes a DFA in the context of {topic}?",
                options=[
                    "A DFA can have multiple transitions on the same symbol from one state.",
                    "A DFA has exactly one transition per symbol from each state in its alphabet.",
                    "A DFA always needs epsilon transitions.",
                    "A DFA can recognize non-context-free languages.",
                ],
                correct_answer="A DFA has exactly one transition per symbol from each state in its alphabet.",
                explanation="Determinism means the next state is uniquely determined for each symbol.",
                hint="Think about what makes a finite automaton deterministic.",
            )

        return QuestionObject(
            question_id=uuid.uuid4().hex[:8],
            type="short_answer",
            concept_tags=[topic],
            difficulty=difficulty,  # type: ignore[arg-type]
            prompt=f"In one or two sentences, explain the core idea of {topic} in automata theory.",
            correct_answer=f"A correct answer should state the essential definition or role of {topic} in automata theory.",
            explanation="A strong answer should be concise, accurate, and conceptually focused.",
            hint="Give the main definition and why the concept matters.",
        )
