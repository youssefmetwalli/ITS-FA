from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, firestore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.video_transcript_service import save_video_transcript


load_dotenv()


def _normalize_service_account_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    private_key = normalized.get("private_key")
    if isinstance(private_key, str):
        normalized["private_key"] = private_key.replace("\\n", "\n")
    return normalized


def _load_firebase_certificate() -> credentials.Certificate:
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


def init_firestore() -> firestore.Client:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(_load_firebase_certificate())
    return firestore.client()


def parse_timestamp(value: str) -> float:
    """Parse SRT/VTT timestamp into seconds."""
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid timestamp: {value}")

    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return (hours * 3600) + (minutes * 60) + seconds


def clean_transcript_text(text: str) -> str:
    """Normalize transcript text for storage."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_srt_or_vtt(content: str) -> list[dict[str, Any]]:
    """Parse SRT/VTT content into timestamped transcript segments."""
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    content = re.sub(r"^\ufeff", "", content)
    content = re.sub(r"^WEBVTT(?:[^\n]*)\n+", "", content, flags=re.IGNORECASE)

    blocks = re.split(r"\n{2,}", content)
    segments: list[dict[str, Any]] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        timing_index = next((idx for idx, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue

        timing_line = lines[timing_index]
        start_raw, end_raw = [part.strip().split()[0] for part in timing_line.split("-->", 1)]
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw)
        text = clean_transcript_text(" ".join(lines[timing_index + 1 :]))
        if not text:
            continue

        segments.append(
            {
                "segment_id": f"segment_{len(segments)}",
                "start": start,
                "duration": max(0.0, end - start),
                "text": text,
            }
        )

    return segments


def parse_txt(content: str) -> list[dict[str, Any]]:
    """Parse plain text into coarse transcript segments."""
    normalized = clean_transcript_text(content)
    if not normalized:
        return []

    chunks: list[str] = []
    words = normalized.split()
    chunk_size = 120
    for index in range(0, len(words), chunk_size):
        chunks.append(" ".join(words[index : index + chunk_size]))

    return [
        {
            "segment_id": f"segment_{index}",
            "start": float(index * 60),
            "duration": 60.0,
            "text": chunk,
        }
        for index, chunk in enumerate(chunks)
        if chunk
    ]


def load_transcript_file(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Load and parse a local transcript file."""
    if not path.exists():
        raise FileNotFoundError(f"Transcript file not found: {path}")

    content = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()

    if suffix in {".srt", ".vtt"}:
        segments = parse_srt_or_vtt(content)
    elif suffix == ".txt":
        segments = parse_txt(content)
    else:
        raise ValueError("Unsupported transcript file type. Use .srt, .vtt, or .txt.")

    transcript_text = clean_transcript_text(" ".join(segment["text"] for segment in segments))
    if not transcript_text:
        raise ValueError("Transcript file did not contain usable transcript text.")

    return transcript_text, segments


def import_transcript(
    db: firestore.Client,
    video_id: str,
    transcript_text: str,
    transcript_segments: list[dict[str, Any]],
    source_path: Path,
    overwrite: bool,
) -> None:
    video_ref = db.collection("videos").document(video_id)
    video_doc = video_ref.get()
    if not video_doc.exists:
        raise ValueError(f"Video document does not exist: videos/{video_id}")

    existing = video_doc.to_dict() or {}
    if str(existing.get("transcript_text", "")).strip() and not overwrite:
        raise ValueError(
            f"videos/{video_id} already has transcript_text. Re-run with --overwrite to replace it."
        )

    save_video_transcript(db, video_id, transcript_text, transcript_segments)
    video_ref.set(
        {
            "transcript_source": "local_file",
            "transcript_source_file": str(source_path),
        },
        merge=True,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", required=True, help="Firestore videos document id.")
    parser.add_argument("--file", required=True, help="Path to .srt, .vtt, or .txt transcript file.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing transcript_text if present.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    transcript_path = Path(args.file)
    transcript_text, transcript_segments = load_transcript_file(transcript_path)
    db = init_firestore()
    import_transcript(
        db=db,
        video_id=args.video_id,
        transcript_text=transcript_text,
        transcript_segments=transcript_segments,
        source_path=transcript_path,
        overwrite=args.overwrite,
    )

    print(
        f"Imported transcript for videos/{args.video_id}: "
        f"{len(transcript_text)} characters, {len(transcript_segments)} segments."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
