import base64
import json
import logging
import os
import random
import re
import time

import firebase_admin
import google.generativeai as genai
from dotenv import load_dotenv
from firebase_admin import credentials, firestore

from services.adaptive_flashcard_service import LEVELS, validate_adaptive_flashcards


load_dotenv()
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
GENERATION_MODEL_NAME = os.environ.get(
    "GENERATION_MODEL_NAME",
    os.environ.get("CHAT_MODEL_NAME", "gemini-2.5-flash-lite"),
)
model = genai.GenerativeModel(GENERATION_MODEL_NAME)


def _normalize_service_account_payload(payload: dict) -> dict:
    """Normalize service account JSON so PEM keys load correctly."""
    normalized = dict(payload)
    private_key = normalized.get("private_key")
    if isinstance(private_key, str):
        normalized["private_key"] = private_key.replace("\\n", "\n")
    return normalized


def _load_firebase_certificate() -> credentials.Certificate:
    """Load Firebase credentials from env first, then local JSON files."""
    creds_b64 = os.environ.get("GOOGLE_CREDS_B64")
    if creds_b64:
        payload = json.loads(base64.b64decode(creds_b64))
        return credentials.Certificate(_normalize_service_account_payload(payload))

    for candidate in ("firebase.json", "service-account.json"):
        if not os.path.exists(candidate):
            continue
        with open(candidate, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return credentials.Certificate(_normalize_service_account_payload(payload))

    raise RuntimeError(
        "No Firebase credentials found. Set GOOGLE_CREDS_B64 or provide firebase.json/service-account.json."
    )


cred = _load_firebase_certificate()
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


GENERATION_PROMPT = """You are generating adaptive flashcard quiz questions for one chapter of an Automata Theory tutoring system.

Chapter title:
{chapter_title}

Source content:
{full_chapter_text}

Generate exactly 20 distinct flashcard questions grouped by difficulty:
- 5 easy
- 5 medium
- 5 hard
- 5 master

Difficulty definitions:
- easy: basic recall or simple conceptual recognition
- medium: conceptual understanding and simple application
- hard: deeper reasoning, comparison, or multi-step thinking
- master: advanced reasoning, tricky distinctions, or synthesis of ideas

Requirements:
- Every question must be grounded in the chapter content.
- Questions must be distinct and non-repetitive.
- Do not generate duplicate concepts phrased differently unless the reasoning demand is clearly different.
- Each question must include:
  - id
  - question
  - options (exactly 4)
  - correct_answer
  - hint
  - explanation
  - concept
  - difficulty
- The value of difficulty must be one of: easy, medium, hard, master
- The correct_answer must exactly match one of the 4 options
- Each options array must contain exactly 4 distinct choices
- Use plain ASCII only.
- Return valid JSON only, with no markdown fences and no extra commentary, in this format:

{{
  "easy": [...5 questions...],
  "medium": [...5 questions...],
  "hard": [...5 questions...],
  "master": [...5 questions...]
}}"""


LEVEL_DIFFICULTY_GUIDANCE = {
    "easy": "basic recall or simple conceptual recognition",
    "medium": "conceptual understanding and simple application",
    "hard": "deeper reasoning, comparison, or multi-step thinking",
    "master": "advanced reasoning, tricky distinctions, or synthesis of ideas",
}


LEVEL_GENERATION_PROMPT = """You are generating adaptive flashcard quiz questions for one chapter of an Automata Theory tutoring system.

Chapter title:
{chapter_title}

Source content:
{full_chapter_text}

Generate exactly 5 distinct {difficulty} flashcard questions.

Difficulty definition:
- {difficulty}: {difficulty_guidance}

Requirements:
- Every question must be grounded in the chapter content.
- Questions must be distinct and non-repetitive.
- Do not generate duplicate concepts phrased differently unless the reasoning demand is clearly different.
- Each question must include:
  - id
  - question
  - options (exactly 4)
  - correct_answer
  - hint
  - explanation
  - concept
  - difficulty
- The value of difficulty must be exactly "{difficulty}"
- The correct_answer must exactly match one of the 4 options
- Each options array must contain exactly 4 distinct choices
- Use plain ASCII only.
- Return valid JSON only, with no markdown fences and no extra commentary, in this format:

{{
  "{difficulty}": [...5 questions...]
}}"""


def _extract_json_payload(text: str) -> dict:
    stripped = text.strip()
    candidates: list[str] = []

    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    if not candidates:
        raise ValueError("No JSON object found in Gemini response.")

    seen: set[str] = set()
    last_error: Exception | None = None
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)

        payload = re.sub(r"^```json\s*", "", candidate, flags=re.IGNORECASE)
        payload = re.sub(r"\s*```$", "", payload, flags=re.IGNORECASE)
        payload = payload.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")

        payload_variants = [
            payload,
            re.sub(r",(\s*[}\]])", r"\1", payload),
            re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", re.sub(r",(\s*[}\]])", r"\1", payload)),
        ]

        for variant in payload_variants:
            try:
                return json.loads(variant)
            except json.JSONDecodeError as exc:
                last_error = exc

    if last_error is not None:
        raise last_error
    raise ValueError("Unable to parse Gemini response as JSON.")


def _generate_flashcards_from_prompt(
    prompt_template: str,
    prompt_values: dict[str, str],
    max_retries: int = 5,
    base_delay: int = 1,
    max_delay: int = 16,
) -> dict:
    retries = 0
    while retries <= max_retries:
        try:
            prompt = prompt_template
            if retries > 0:
                prompt += (
                    "\n\nPrevious attempt returned malformed JSON."
                    " Return one valid JSON object only."
                    " Do not include prose, comments, markdown fences, or trailing commas."
                )

            response = model.generate_content(
                prompt.format(**prompt_values),
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=4000,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            if not response.text:
                raise ValueError("Gemini returned an empty response.")

            payload = _extract_json_payload(response.text)
            return payload
        except (json.JSONDecodeError, ValueError) as exc:
            retries += 1
            if retries > max_retries:
                logging.error("Adaptive flashcard generation failed after JSON retries: %s", exc)
                raise
            logging.warning(
                "Adaptive flashcard JSON was malformed on attempt %s/%s. Retrying. Error: %s",
                retries,
                max_retries,
                exc,
            )
            continue
        except Exception as exc:
            if "429" in str(exc):
                retries += 1
                delay = min(base_delay * (2 ** retries) + random.uniform(0, 1), max_delay)
                logging.info("Rate limited generating flashcards. Retrying in %.2f seconds.", delay)
                time.sleep(delay)
                continue
            response_text = getattr(locals().get("response", None), "text", "")
            if response_text:
                logging.error("Last Gemini response preview: %s", response_text[:600])
            logging.error("Adaptive flashcard generation failed: %s", exc)
            raise

    raise RuntimeError("Max retries exceeded while generating adaptive flashcards.")


def generate_adaptive_flashcards_for_level(
    chapter_title: str,
    full_chapter_text: str,
    difficulty: str,
    max_retries: int = 5,
    base_delay: int = 1,
    max_delay: int = 16,
) -> list[dict]:
    payload = _generate_flashcards_from_prompt(
        LEVEL_GENERATION_PROMPT,
        {
            "chapter_title": chapter_title,
            "full_chapter_text": full_chapter_text,
            "difficulty": difficulty,
            "difficulty_guidance": LEVEL_DIFFICULTY_GUIDANCE[difficulty],
        },
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )

    if not isinstance(payload, dict):
        raise ValueError(f"Gemini returned an invalid payload for {difficulty}.")
    level_cards = payload.get(difficulty)
    if not isinstance(level_cards, list) or len(level_cards) != 5:
        raise ValueError(f"Gemini returned an invalid number of {difficulty} flashcards.")

    normalized_cards: list[dict] = []
    seen_ids: set[str] = set()
    for index, card in enumerate(level_cards, start=1):
        normalized_card = dict(card)
        raw_id = str(normalized_card.get("id", "")).strip()
        if not raw_id:
            raw_id = f"{difficulty}_{index}"

        normalized_id = f"{difficulty}_{raw_id}"
        if normalized_id in seen_ids:
            normalized_id = f"{difficulty}_{raw_id}_{index}"

        seen_ids.add(normalized_id)
        normalized_card["id"] = normalized_id
        normalized_card["difficulty"] = difficulty

        raw_options = normalized_card.get("options", [])
        if not isinstance(raw_options, list):
            raw_options = []

        deduped_options: list[str] = []
        seen_options: set[str] = set()
        for option in raw_options:
            option_text = str(option).strip()
            if not option_text:
                continue
            option_key = option_text.casefold()
            if option_key in seen_options:
                continue
            seen_options.add(option_key)
            deduped_options.append(option_text)

        correct_answer = str(normalized_card.get("correct_answer", "")).strip()
        if correct_answer:
            correct_key = correct_answer.casefold()
            if correct_key not in seen_options:
                deduped_options.insert(0, correct_answer)
                seen_options.add(correct_key)

        filler_index = 1
        while len(deduped_options) < 4:
            filler_option = f"None of the above ({difficulty} distractor {filler_index})"
            filler_index += 1
            filler_key = filler_option.casefold()
            if filler_key in seen_options:
                continue
            seen_options.add(filler_key)
            deduped_options.append(filler_option)

        normalized_card["options"] = deduped_options[:4]
        if correct_answer and correct_answer not in normalized_card["options"]:
            normalized_card["options"][0] = correct_answer

        normalized_cards.append(normalized_card)
    return normalized_cards


def generate_adaptive_flashcards(
    chapter_title: str,
    full_chapter_text: str,
    max_retries: int = 5,
    base_delay: int = 1,
    max_delay: int = 16,
) -> dict[str, list[dict]]:
    flashcards_by_level: dict[str, list[dict]] = {}
    for difficulty in LEVELS:
        logging.info("Generating %s flashcards for %s...", difficulty, chapter_title)
        flashcards_by_level[difficulty] = generate_adaptive_flashcards_for_level(
            chapter_title,
            full_chapter_text,
            difficulty,
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
        )

    return validate_adaptive_flashcards(flashcards_by_level)


def save_adaptive_flashcards(chapter_id: int, flashcards: dict[str, list[dict]]) -> None:
    db.collection("chapters").document(str(chapter_id)).set(
        {"adaptive_flashcards": flashcards},
        merge=True,
    )
    logging.info("Saved adaptive flashcards for chapter %s.", chapter_id)


def process_chapters_for_adaptive_flashcards(batch_size: int = 5, start_chapter: int = 38, end_chapter: int = 48) -> None:
    chapters_ref = db.collection("chapters").order_by("id")
    chapters = [doc.to_dict() for doc in chapters_ref.stream() if start_chapter <= int(doc.id) <= end_chapter]

    for batch_start in range(0, len(chapters), batch_size):
        batch = chapters[batch_start:batch_start + batch_size]
        logging.info("Processing adaptive flashcards batch %s to %s...", batch_start + 1, batch_start + len(batch))

        for chapter in batch:
            chapter_id = chapter["id"]
            chapter_title = chapter.get("title", f"Chapter {chapter_id}")

            if "Summary and References" in chapter_title:
                logging.info("Skipping chapter %s because it is a summary/reference chapter.", chapter_id)
                continue

            subchapters_ref = db.collection("subchapters").where("chapter_id", "==", chapter_id)
            subchapter_docs = subchapters_ref.stream()
            full_chapter_text = ""

            for subchapter_doc in subchapter_docs:
                subchapter = subchapter_doc.to_dict()
                summary_text = subchapter.get("summary", {}).get("text", "")
                if summary_text:
                    full_chapter_text += summary_text + "\n"

            if not full_chapter_text.strip():
                logging.info("Skipping chapter %s because no summaries were found.", chapter_id)
                continue

            flashcards = generate_adaptive_flashcards(chapter_title, full_chapter_text)
            save_adaptive_flashcards(chapter_id, flashcards)


if __name__ == "__main__":
    process_chapters_for_adaptive_flashcards()
