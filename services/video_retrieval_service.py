"""Transcript retrieval and Q&A for video learning."""

from __future__ import annotations

import logging
import os
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings


load_dotenv()
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))


class VideoRetrievalService:
    """Keeps transcript retrieval distinct from textbook retrieval."""

    def __init__(self, vector_root: str = "video_vector_db") -> None:
        self.vector_root = vector_root
        self._embeddings: GoogleGenerativeAIEmbeddings | None = None
        self.model = genai.GenerativeModel(os.environ.get("CHAT_MODEL_NAME", "gemini-2.5-flash-lite"))

    def _ensure_embeddings(self) -> GoogleGenerativeAIEmbeddings:
        if self._embeddings is None:
            self._embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
        return self._embeddings

    def _video_vector_path(self, video_id: str) -> str:
        return os.path.join(self.vector_root, video_id)

    def _build_chunk_payloads(self, video_record: dict[str, Any]) -> list[dict[str, Any]]:
        transcript_segments = list(video_record.get("transcript_segments", []))
        if transcript_segments:
            chunks: list[dict[str, Any]] = []
            current_parts: list[str] = []
            current_start = 0.0
            current_end = 0.0

            for segment in transcript_segments:
                text = str(segment.get("text", "")).strip()
                if not text:
                    continue
                start = float(segment.get("start", 0.0))
                end = start + float(segment.get("duration", 0.0))
                if not current_parts:
                    current_start = start
                candidate_parts = current_parts + [text]
                candidate_text = " ".join(candidate_parts)
                if len(candidate_text) > 1000 and current_parts:
                    chunks.append(
                        {
                            "text": " ".join(current_parts),
                            "metadata": {"start": current_start, "end": current_end},
                        }
                    )
                    current_parts = [text]
                    current_start = start
                else:
                    current_parts = candidate_parts
                current_end = end

            if current_parts:
                chunks.append(
                    {
                        "text": " ".join(current_parts),
                        "metadata": {"start": current_start, "end": current_end},
                    }
                )
            if chunks:
                return chunks

        transcript_text = str(video_record.get("transcript_text", "")).strip()
        if not transcript_text:
            return []

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=120)
        return [{"text": chunk, "metadata": {}} for chunk in splitter.split_text(transcript_text)]

    def ensure_vector_store(self, video_record: dict[str, Any], force_rebuild: bool = False) -> Any | None:
        """Load or build the transcript vector store for one video."""
        video_id = str(video_record.get("id", "")).strip()
        transcript_text = str(video_record.get("transcript_text", "")).strip()
        if not video_id or not transcript_text:
            return None

        vector_path = self._video_vector_path(video_id)
        index_path = os.path.join(vector_path, "index.faiss")
        embeddings = self._ensure_embeddings()

        if os.path.exists(index_path) and not force_rebuild:
            try:
                return FAISS.load_local(vector_path, embeddings, allow_dangerous_deserialization=True)
            except Exception as exc:
                logging.warning("Failed loading video vector store for %s: %s", video_id, exc)

        chunk_payloads = self._build_chunk_payloads(video_record)
        if not chunk_payloads:
            return None

        os.makedirs(vector_path, exist_ok=True)
        vector_store = FAISS.from_texts(
            texts=[payload["text"] for payload in chunk_payloads],
            embedding=embeddings,
            metadatas=[payload["metadata"] for payload in chunk_payloads],
        )
        vector_store.save_local(vector_path)
        return vector_store

    def retrieve(self, video_record: dict[str, Any], query: str, k: int = 4) -> list[dict[str, Any]]:
        """Retrieve transcript chunks relevant to a question."""
        vector_store = self.ensure_vector_store(video_record)
        if vector_store is None:
            return []

        try:
            docs = vector_store.similarity_search(query, k=k)
        except Exception as exc:
            logging.error("Video retrieval failed for %s: %s", video_record.get("id"), exc)
            return []

        results: list[dict[str, Any]] = []
        for doc in docs:
            metadata = getattr(doc, "metadata", {}) or {}
            results.append(
                {
                    "content": getattr(doc, "page_content", "") or "",
                    "start": metadata.get("start"),
                    "end": metadata.get("end"),
                }
            )
        return results

    def answer_video_question(self, video_record: dict[str, Any], user_message: str) -> dict[str, Any]:
        """Answer a learner question using the selected video's transcript as primary context."""
        video_id = str(video_record.get("id", "")).strip()
        transcript_text = str(video_record.get("transcript_text", "")).strip()
        if not transcript_text:
            return {
                "response_text": "The video transcript is not available yet, so I cannot answer video-specific questions reliably.",
                "source_type": "video",
                "selected_video_id": video_id,
            }

        retrieved_chunks = self.retrieve(video_record, user_message, k=4)
        transcript_context = "\n\n".join(chunk["content"] for chunk in retrieved_chunks if chunk["content"]).strip()
        if not transcript_context:
            transcript_context = transcript_text[:2500]

        prompt = f"""You are answering a question about a single automata theory learning video.
Stay grounded in the provided transcript context only.
If the transcript context is insufficient, say so explicitly.
Do not invent video-specific details.

Video title: {video_record.get("title", "")}

Learner question:
{user_message}

Transcript context:
{transcript_context}
"""

        try:
            response = self.model.generate_content(prompt)
            response_text = (response.text or "").strip()
        except Exception as exc:
            logging.error("Video Q&A generation failed for %s: %s", video_id, exc)
            response_text = (
                "I could not generate a grounded answer from the video transcript right now. "
                "Please try again in a moment."
            )

        if not response_text:
            response_text = "I could not find enough transcript context to answer that reliably."

        return {
            "response_text": response_text,
            "source_type": "video",
            "selected_video_id": video_id,
        }
