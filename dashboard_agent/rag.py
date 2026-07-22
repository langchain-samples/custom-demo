"""In-memory RAG over the dummy corpus.

Dependency-free TF-IDF cosine similarity — no embeddings service, no vector DB.
This is deliberately a "dummy" retriever: it is deterministic, offline, and fast,
so the agent and its tests never depend on an external index.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from typing import Any

from .corpus import CORPUS, Document

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "what", "which", "can", "you", "provide", "latest", "over", "last", "as",
    "from", "relevant", "according", "outlined", "available", "data", "report",
    "reports", "un", "with", "at", "by", "about", "their", "there", "this",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _doc_blob(doc: Document) -> str:
    """The searchable surface of a document."""
    return " ".join(
        [doc["title"], doc["region"], doc["topic"], doc["period"], doc["text"]]
    )


@lru_cache(maxsize=1)
def _index() -> tuple[list[Counter], dict[str, float]]:
    """Build (per-doc term-frequency vectors, inverse-document-frequency map)."""
    tf_vectors: list[Counter] = []
    df: Counter = Counter()
    for doc in CORPUS:
        tokens = _tokenize(_doc_blob(doc))
        tf = Counter(tokens)
        tf_vectors.append(tf)
        for term in tf:
            df[term] += 1
    n_docs = len(CORPUS)
    idf = {term: math.log((n_docs + 1) / (count + 1)) + 1.0 for term, count in df.items()}
    return tf_vectors, idf


def _tfidf_vector(tf: Counter, idf: dict[str, float]) -> dict[str, float]:
    return {term: freq * idf.get(term, 0.0) for term, freq in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search(query: str, k: int = 3, min_score: float = 0.01) -> list[dict[str, Any]]:
    """Return the top-k corpus documents most relevant to `query`.

    Each result includes the human-readable text (for grounding/citation) and
    the structured `data` block (for building visualizations).
    """
    tf_vectors, idf = _index()
    q_vec = _tfidf_vector(Counter(_tokenize(query)), idf)

    scored: list[tuple[float, Document]] = []
    for doc, tf in zip(CORPUS, tf_vectors):
        score = _cosine(q_vec, _tfidf_vector(tf, idf))
        if score >= min_score:
            scored.append((score, doc))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    results: list[dict[str, Any]] = []
    for score, doc in scored[:k]:
        results.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "source": doc["source"],
                "region": doc["region"],
                "period": doc["period"],
                "text": doc["text"],
                "data": doc["data"],
                "score": round(score, 4),
            }
        )
    return results
