from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import URLError

from firebase_admin import firestore

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:  # pragma: no cover - optional dependency
    YouTubeTranscriptApi = None

try:  # pragma: no cover - optional dependency details vary by installed version
    from youtube_transcript_api._errors import (
        NoTranscriptAvailable,
        TranscriptsDisabled,
        VideoUnavailable,
    )
except ImportError:  # pragma: no cover - fallback if internals move
    NoTranscriptAvailable = TranscriptsDisabled = VideoUnavailable = tuple()  # type: ignore[assignment]


YOUTUBE_PATTERNS = [
    r"(?:youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})",
    r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
    r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})",
]


def extract_youtube_video_id(url: str) -> str | None:
    """Extract a YouTube video id from common URL forms."""
    if not url:
        return None
    for pattern in YOUTUBE_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def normalize_transcript_segments(raw_segments: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Normalize transcript segments into plain text and timestamped records."""
    cleaned_segments: list[dict[str, Any]] = []
    parts: list[str] = []
    for index, segment in enumerate(raw_segments):
        text = re.sub(r"\s+", " ", str(segment.get("text", "")).strip())
        if not text:
            continue
        normalized = {
            "segment_id": str(segment.get("segment_id") or f"segment_{index}"),
            "start": float(segment.get("start", 0.0)),
            "duration": float(segment.get("duration", 0.0)),
            "text": text,
        }
        cleaned_segments.append(normalized)
        parts.append(text)
    return " ".join(parts).strip(), cleaned_segments


def fetch_video_transcript(video_id: str) -> tuple[str, list[dict[str, Any]], str | None]:
    """Fetch a YouTube transcript when the dependency and transcript are available."""
    if not video_id:
        return "", [], "The video does not have a valid YouTube video id."
    if YouTubeTranscriptApi is None:
        return "", [], "The youtube-transcript-api package is not installed."

    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None

        try:
            transcript = transcripts.find_transcript(["en"])
        except Exception:
            transcript = None

        if transcript is None:
            try:
                transcript = transcripts.find_generated_transcript(["en"])
            except Exception:
                transcript = None

        if transcript is None:
            available_transcripts = list(transcripts)
            if available_transcripts:
                transcript = available_transcripts[0]

        if transcript is None:
            return "", [], "No transcript is available for this video."

        raw_segments = transcript.fetch()
    except (NoTranscriptAvailable, TranscriptsDisabled, VideoUnavailable):
        return "", [], "No transcript is available for this video."
    except (ConnectionError, TimeoutError, URLError):
        return "", [], "Network error while contacting YouTube for the transcript."
    except json.JSONDecodeError:
        return "", [], "Transcript API parsing failed."
    except Exception as exc:
        message = str(exc).strip()
        lowered = message.lower()
        if "no element found" in lowered or "json" in lowered or "decode" in lowered:
            return "", [], "Transcript API parsing failed."
        if "failed to resolve" in lowered or "nameresolutionerror" in lowered or "httpsconnectionpool" in lowered:
            return "", [], "Network error while contacting YouTube for the transcript."
        if "transcript" in lowered and ("disabled" in lowered or "unavailable" in lowered or "could not retrieve" in lowered):
            return "", [], "No transcript is available for this video."
        return "", [], f"Transcript retrieval failed: {message or exc.__class__.__name__}"

    transcript_text, transcript_segments = normalize_transcript_segments(raw_segments)
    if not transcript_text:
        return "", [], "Transcript retrieval returned no usable text."
    return transcript_text, transcript_segments, None


def save_video_transcript(
    db: Any,
    video_id: str,
    transcript_text: str,
    transcript_segments: list[dict[str, Any]],
) -> None:
    """Persist transcript fields onto the video record."""
    db.collection("videos").document(video_id).set(
        {
            "transcript_text": transcript_text,
            "transcript_segments": transcript_segments,
            "transcript_updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )


def ensure_video_transcript(db: Any, video_record: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Ensure a video record has transcript text, attempting YouTube fetch when needed."""
    transcript_text = str(video_record.get("transcript_text", "")).strip()
    if transcript_text:
        return video_record, None

    youtube_video_id = str(video_record.get("youtube_video_id", "")).strip()
    if not youtube_video_id:
        youtube_video_id = extract_youtube_video_id(str(video_record.get("url", "")).strip()) or ""

    if not youtube_video_id:
        return video_record, "Transcript unavailable because the video link is missing a YouTube id."

    transcript_text, transcript_segments, error_message = fetch_video_transcript(youtube_video_id)
    if error_message:
        updated_record = dict(video_record)
        updated_record["youtube_video_id"] = youtube_video_id
        return updated_record, error_message

    save_video_transcript(db, str(video_record.get("id", "")), transcript_text, transcript_segments)
    updated_record = dict(video_record)
    updated_record["youtube_video_id"] = youtube_video_id
    updated_record["transcript_text"] = transcript_text
    updated_record["transcript_segments"] = transcript_segments
    return updated_record, None
