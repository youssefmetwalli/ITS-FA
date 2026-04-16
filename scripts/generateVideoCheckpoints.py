from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv
from firebase_admin import firestore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_video_transcript import init_firestore, load_transcript_file


load_dotenv()
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

GENERATION_MODEL_NAME = os.environ.get(
    "GENERATION_MODEL_NAME",
    os.environ.get("CHAT_MODEL_NAME", "gemini-2.5-flash-lite"),
)

TARGET_CHECKPOINT_COUNT = 5
TARGET_RATIOS = (0.12, 0.3, 0.48, 0.66, 0.84)
MAX_GENERATION_RETRIES = 4


def _extract_json_payload(text: str) -> Any:
    cleaned = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("No JSON payload found in Gemini response.")
        payload = json.loads(match.group(0))

    if isinstance(payload, dict):
        for key in ("checkpoint_questions", "checkpoints", "questions", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return payload


def _format_seconds(total_seconds: float) -> str:
    seconds = max(0, int(total_seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _estimate_duration(segments: list[dict[str, Any]]) -> float:
    if not segments:
        return 0.0
    last_segment = segments[-1]
    return float(last_segment.get("start", 0.0)) + float(last_segment.get("duration", 0.0))


def _segments_near_timestamp(
    segments: list[dict[str, Any]],
    timestamp_seconds: float,
    *,
    max_segments: int = 5,
    window_seconds: float = 75.0,
) -> list[dict[str, Any]]:
    nearby = [
        segment
        for segment in segments
        if abs(float(segment.get("start", 0.0)) - timestamp_seconds) <= window_seconds
    ]
    if nearby:
        return nearby[:max_segments]

    nearest = sorted(
        segments,
        key=lambda segment: abs(float(segment.get("start", 0.0)) - timestamp_seconds),
    )
    return nearest[:max_segments]


def _build_checkpoint_windows(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    duration = _estimate_duration(segments)
    if duration <= 0:
        raise ValueError("Transcript segments do not contain usable timestamps.")

    windows: list[dict[str, Any]] = []
    for index, ratio in enumerate(TARGET_RATIOS):
        timestamp = max(15.0, min(duration - 15.0, duration * ratio))
        nearby_segments = _segments_near_timestamp(segments, timestamp)
        excerpt = " ".join(str(segment.get("text", "")).strip() for segment in nearby_segments).strip()
        if not excerpt:
            continue

        nearest_segment_start = min(
            (float(segment.get("start", timestamp)) for segment in nearby_segments),
            default=timestamp,
        )
        windows.append(
            {
                "index": index + 1,
                "timestamp_seconds": int(round(nearest_segment_start)),
                "timestamp_label": _format_seconds(nearest_segment_start),
                "excerpt": excerpt[:1800],
            }
        )

    if len(windows) != TARGET_CHECKPOINT_COUNT:
        raise ValueError("Could not derive five checkpoint windows from transcript timestamps.")

    return windows


def _normalize_true_false_answer(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "t", "yes"}:
        return "True"
    if normalized in {"false", "f", "no"}:
        return "False"
    raise ValueError(f"Invalid true/false answer: {value}")


def _validate_checkpoint_payload(payload: Any, windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) != TARGET_CHECKPOINT_COUNT:
        raise ValueError("Gemini response must be a JSON array with exactly five checkpoint objects.")

    checkpoint_questions: list[dict[str, Any]] = []
    window_map = {window["index"]: window for window in windows}

    for response_index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError("Each checkpoint entry must be an object.")

        raw_window_index = item.get("window_index")
        if raw_window_index in (None, ""):
            window_index = response_index + 1
        else:
            window_index = int(raw_window_index)
            if window_index in range(0, TARGET_CHECKPOINT_COUNT):
                window_index += 1
        if window_index not in window_map:
            raise ValueError(f"Unexpected window_index in checkpoint output: {window_index}")

        window = window_map[window_index]
        question = str(item.get("question", "")).strip()
        explanation = str(item.get("explanation", "")).strip()
        if not question:
            raise ValueError(f"Checkpoint question missing for window {window_index}.")
        if not explanation:
            raise ValueError(f"Checkpoint explanation missing for window {window_index}.")

        checkpoint_questions.append(
            {
                "checkpoint_id": f"checkpoint_{window_index}",
                "timestamp_seconds": int(window["timestamp_seconds"]),
                "question": question,
                "options": ["True", "False"],
                "correct_answer": _normalize_true_false_answer(item.get("correct_answer")),
                "explanation": explanation,
            }
        )

    checkpoint_questions.sort(key=lambda item: item["timestamp_seconds"])
    return checkpoint_questions


def generate_checkpoint_questions(
    model: genai.GenerativeModel,
    *,
    video_title: str,
    transcript_text: str,
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    window_text = "\n\n".join(
        (
            f"Window {window['index']}\n"
            f"Timestamp: {window['timestamp_label']} ({window['timestamp_seconds']} seconds)\n"
            f"Excerpt: {window['excerpt']}"
        )
        for window in windows
    )

    prompt = f"""You are generating checkpoint pop questions for a video learning system in an automata theory tutor.

Return valid JSON only as an array with exactly {TARGET_CHECKPOINT_COUNT} objects.
Create exactly one question for each transcript window.

Each object must contain exactly these keys:
- window_index
- question
- correct_answer
- explanation

Rules:
- Each question must be true/false style.
- The wording should make the learner think about the concept just explained in that window.
- correct_answer must be exactly "True" or "False".
- explanation must be short, clear, and grounded in the excerpt.
- Keep questions concise.
- Do not ask about content outside the excerpt.
- Make all five questions distinct.

Video title: {video_title}

Transcript overview:
{transcript_text[:3500]}

Checkpoint windows:
{window_text}
"""

    last_error: Exception | None = None
    for attempt in range(1, MAX_GENERATION_RETRIES + 1):
        try:
            response = model.generate_content(prompt)
            payload = _extract_json_payload(response.text or "")
            return _validate_checkpoint_payload(payload, windows)
        except Exception as exc:
            last_error = exc
            if attempt == MAX_GENERATION_RETRIES:
                break
            time.sleep(min(6.0, (1.25 ** attempt) + random.uniform(0.1, 0.6)))

    raise ValueError(f"Checkpoint generation failed after {MAX_GENERATION_RETRIES} attempts: {last_error}")


def save_checkpoint_questions(
    db: firestore.Client,
    *,
    video_id: str,
    checkpoint_questions: list[dict[str, Any]],
    source_path: Path,
) -> None:
    db.collection("videos").document(video_id).set(
        {
            "checkpoint_questions": checkpoint_questions,
            "checkpoint_generation_source": "local_transcript_file",
            "checkpoint_source_file": str(source_path),
            "checkpoint_generation_model": GENERATION_MODEL_NAME,
            "checkpoint_updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )


def _load_video_record(db: firestore.Client, video_id: str) -> dict[str, Any]:
    doc = db.collection("videos").document(video_id).get()
    if not doc.exists:
        raise ValueError(f"Video document does not exist: videos/{video_id}")
    data = doc.to_dict() or {}
    data["id"] = data.get("id") or doc.id
    return data


def process_video(
    db: firestore.Client,
    model: genai.GenerativeModel,
    *,
    video_id: str,
    file_path: Path,
    overwrite: bool,
) -> tuple[int, list[dict[str, Any]]]:
    video_record = _load_video_record(db, video_id)
    existing_checkpoints = video_record.get("checkpoint_questions") or []
    if existing_checkpoints and not overwrite:
        raise ValueError(
            f"videos/{video_id} already has checkpoint_questions. Re-run with --overwrite to replace them."
        )

    transcript_text, transcript_segments = load_transcript_file(file_path)
    windows = _build_checkpoint_windows(transcript_segments)
    checkpoint_questions = generate_checkpoint_questions(
        model,
        video_title=str(video_record.get("title", "")).strip() or video_id,
        transcript_text=transcript_text,
        windows=windows,
    )
    save_checkpoint_questions(
        db,
        video_id=video_id,
        checkpoint_questions=checkpoint_questions,
        source_path=file_path,
    )
    return len(transcript_segments), checkpoint_questions


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", help="Firestore videos document id.")
    parser.add_argument("--file", help="Path to the local transcript file.")
    parser.add_argument(
        "--directory",
        default="sources/srts",
        help="Directory containing transcript files named <video_id>.srt/.vtt/.txt.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate checkpoints for every transcript file in the directory that matches a video id.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace existing checkpoint_questions if present.")
    return parser


def _discover_input_files(directory: Path) -> list[tuple[str, Path]]:
    supported_files: list[tuple[str, Path]] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".srt", ".vtt", ".txt"} or not path.is_file():
            continue
        supported_files.append((path.stem, path))
    return supported_files


def main() -> int:
    args = build_arg_parser().parse_args()
    db = init_firestore()
    model = genai.GenerativeModel(GENERATION_MODEL_NAME)

    jobs: list[tuple[str, Path]] = []
    if args.all:
        transcript_dir = Path(args.directory)
        if not transcript_dir.exists():
            raise FileNotFoundError(f"Transcript directory not found: {transcript_dir}")
        jobs = _discover_input_files(transcript_dir)
        if not jobs:
            raise ValueError(f"No transcript files found in {transcript_dir}")
    else:
        if not args.video_id or not args.file:
            raise ValueError("Use --video-id and --file, or use --all with --directory.")
        jobs = [(args.video_id, Path(args.file))]

    failures: list[str] = []
    for video_id, file_path in jobs:
        try:
            segment_count, checkpoint_questions = process_video(
                db,
                model,
                video_id=video_id,
                file_path=file_path,
                overwrite=args.overwrite,
            )
            print(
                f"Generated checkpoints for videos/{video_id}: "
                f"{len(checkpoint_questions)} questions from {segment_count} segments."
            )
        except Exception as exc:
            failures.append(f"{video_id}: {exc}")

    if failures:
        for failure in failures:
            print(f"Failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
