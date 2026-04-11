"""Shared retrieval service over the existing FAISS knowledge base."""

from __future__ import annotations

import logging
from typing import Any

from chatbot import load_or_create_vector_db


class RetrievalService:
    """Provides reusable retrieval over the course vector database."""

    def __init__(self) -> None:
        self._vector_db: Any | None = None

    def _ensure_vector_db(self) -> Any | None:
        if self._vector_db is None:
            self._vector_db = load_or_create_vector_db()
        return self._vector_db

    def retrieve(self, query: str, k: int = 4) -> list[dict[str, str]]:
        """Retrieve relevant automata-theory passages."""
        vector_db = self._ensure_vector_db()
        if vector_db is None:
            logging.warning("Retrieval unavailable: vector database could not be loaded.")
            return []

        try:
            docs = vector_db.similarity_search(query, k=k)
        except Exception as exc:
            logging.error("Retrieval error for query '%s': %s", query, exc)
            return []

        results: list[dict[str, str]] = []
        for doc in docs:
            metadata = getattr(doc, "metadata", {}) or {}
            results.append(
                {
                    "content": getattr(doc, "page_content", "") or "",
                    "source": str(metadata.get("source", "course_material")),
                }
            )
        return results

    def retrieve_text(self, query: str, k: int = 4) -> str:
        """Return concatenated retrieved passages."""
        docs = self.retrieve(query, k=k)
        return "\n\n".join(doc["content"] for doc in docs if doc["content"])
