"""Summary generation helpers for video learning."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv
from firebase_admin import firestore


load_dotenv()
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))


def _extract_json_payload(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON summary found in Gemini response.")
    return json.loads(match.group(0))


class VideoSummaryService:
    """Generates and stores concise educational summaries for videos."""

    def __init__(self) -> None:
        self.model = genai.GenerativeModel(os.environ.get("CHAT_MODEL_NAME", "gemini-2.5-flash-lite"))

    def generate_video_summary(self, video_title: str, transcript_text: str) -> dict[str, Any]:
        """Generate a concise study summary from a transcript."""
        prompt = f"""You are creating a concise study note for one automata theory learning video.
Return valid JSON with exactly these keys:
- narrative_summary
- key_concepts
- important_takeaways

Rules:
- Keep the narrative summary short and educational.
- key_concepts must be a JSON array of short strings.
- important_takeaways must be a JSON array of short bullet-style strings.
- Stay grounded in the transcript only.

Video title: {video_title}

Transcript:
{transcript_text[:12000]}
"""

        response = self.model.generate_content(prompt)
        payload = _extract_json_payload(response.text or "")
        return {
            "narrative_summary": str(payload.get("narrative_summary", "")).strip(),
            "key_concepts": [str(item).strip() for item in payload.get("key_concepts", []) if str(item).strip()],
            "important_takeaways": [
                str(item).strip()
                for item in payload.get("important_takeaways", [])
                if str(item).strip()
            ],
        }

    def save_video_summary(self, db: Any, video_id: str, summary_payload: dict[str, Any]) -> None:
        """Persist a generated summary to Firestore."""
        db.collection("videos").document(video_id).set(
            {
                "generated_summary": summary_payload,
                "summary_updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    def load_or_generate_summary(
        self,
        db: Any,
        video_record: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Reuse a saved summary when possible, otherwise generate and persist a new one."""
        existing_summary = video_record.get("generated_summary")
        if isinstance(existing_summary, dict) and existing_summary.get("narrative_summary"):
            return existing_summary, None

        transcript_text = str(video_record.get("transcript_text", "")).strip()
        if not transcript_text:
            return None, "A transcript is required before a study summary can be generated."

        try:
            summary_payload = self.generate_video_summary(
                str(video_record.get("title", "")).strip(),
                transcript_text,
            )
        except Exception as exc:
            return None, f"Summary generation failed: {exc}"

        self.save_video_summary(db, str(video_record.get("id", "")), summary_payload)
        return summary_payload, None
