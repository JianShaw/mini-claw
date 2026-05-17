"""Hybrid retrieval for Mini Claw memory.

The retriever keeps memory injection bounded by ranking small markdown chunks
instead of passing whole memory files to the model.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


@dataclass(slots=True)
class MemoryChunk:
    source: str
    title: str
    text: str


@dataclass(slots=True)
class MemorySearchResult:
    chunk: MemoryChunk
    score: float
    semantic_score: float
    bm25_score: float


class HybridMemorySearch:
    """Rank memory chunks with 70% vector-style cosine and 30% BM25."""

    def __init__(
        self,
        *,
        semantic_weight: float = 0.7,
        bm25_weight: float = 0.3,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        total = semantic_weight + bm25_weight
        if total <= 0:
            raise ValueError("At least one search weight must be positive")
        self.semantic_weight = semantic_weight / total
        self.bm25_weight = bm25_weight / total
        self.k1 = k1
        self.b = b

    def search(
        self,
        query: str,
        chunks: list[MemoryChunk],
        *,
        top_k: int = 8,
    ) -> list[MemorySearchResult]:
        if top_k <= 0 or not chunks:
            return []

        query_terms = _tokens(query)
        if not query_terms:
            return [
                MemorySearchResult(chunk=chunk, score=0.0, semantic_score=0.0, bm25_score=0.0)
                for chunk in chunks[:top_k]
            ]

        docs = [_tokens(_chunk_text(chunk)) for chunk in chunks]
        doc_count = len(docs)
        doc_freq = _document_frequency(docs)
        idf = {
            term: math.log((doc_count - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in doc_freq.items()
        }
        avg_len = sum(len(doc) for doc in docs) / doc_count if doc_count else 0.0

        semantic_scores = [
            _cosine_tfidf(query_terms, doc, idf)
            for doc in docs
        ]
        bm25_scores = [
            self._bm25(query_terms, doc, idf, avg_len)
            for doc in docs
        ]

        norm_semantic = _normalize(semantic_scores)
        norm_bm25 = _normalize(bm25_scores)
        results: list[MemorySearchResult] = []
        for idx, chunk in enumerate(chunks):
            score = (
                self.semantic_weight * norm_semantic[idx]
                + self.bm25_weight * norm_bm25[idx]
            )
            if score <= 0.0:
                continue
            results.append(MemorySearchResult(
                chunk=chunk,
                score=score,
                semantic_score=semantic_scores[idx],
                bm25_score=bm25_scores[idx],
            ))

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def _bm25(
        self,
        query_terms: list[str],
        doc_terms: list[str],
        idf: dict[str, float],
        avg_len: float,
    ) -> float:
        if not doc_terms:
            return 0.0
        counts = Counter(doc_terms)
        doc_len = len(doc_terms)
        score = 0.0
        for term in set(query_terms):
            freq = counts.get(term, 0)
            if not freq:
                continue
            denom = freq + self.k1 * (1.0 - self.b + self.b * doc_len / max(avg_len, 1.0))
            score += idf.get(term, 0.0) * freq * (self.k1 + 1.0) / denom
        return score


def build_memory_chunks(long_memory: str, daily_memory: str) -> list[MemoryChunk]:
    chunks: list[MemoryChunk] = []
    chunks.extend(_markdown_chunks("Long-Term Memory", long_memory))
    chunks.extend(_markdown_chunks("Today's Daily Memory", daily_memory))
    return chunks


def _markdown_chunks(source: str, markdown: str) -> list[MemoryChunk]:
    title = source
    chunks: list[MemoryChunk] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(line.strip() for line in paragraph if line.strip())
        paragraph.clear()
        if text and not _frontmatter_line(text):
            chunks.append(MemoryChunk(source=source, title=title, text=text))

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        if line.startswith("#"):
            flush_paragraph()
            title = line.lstrip("#").strip() or source
            continue
        if line.startswith("- "):
            flush_paragraph()
            item = line[2:].strip()
            if item and item != "None recorded.":
                chunks.append(MemoryChunk(source=source, title=title, text=item))
            continue
        paragraph.append(line)

    flush_paragraph()
    return chunks


def _chunk_text(chunk: MemoryChunk) -> str:
    return f"{chunk.source} {chunk.title} {chunk.text}"


def _tokens(text: str) -> list[str]:
    basic = [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]
    cjk_chars = [token for token in basic if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    bigrams = [a + b for a, b in zip(cjk_chars, cjk_chars[1:])]
    return basic + bigrams


def _document_frequency(docs: list[list[str]]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for doc in docs:
        for term in set(doc):
            freq[term] = freq.get(term, 0) + 1
    return freq


def _cosine_tfidf(
    query_terms: list[str],
    doc_terms: list[str],
    idf: dict[str, float],
) -> float:
    if not query_terms or not doc_terms:
        return 0.0
    query_vec = _tfidf(query_terms, idf)
    doc_vec = _tfidf(doc_terms, idf)
    dot = sum(weight * doc_vec.get(term, 0.0) for term, weight in query_vec.items())
    q_norm = math.sqrt(sum(weight * weight for weight in query_vec.values()))
    d_norm = math.sqrt(sum(weight * weight for weight in doc_vec.values()))
    if q_norm == 0.0 or d_norm == 0.0:
        return 0.0
    return dot / (q_norm * d_norm)


def _tfidf(terms: list[str], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(terms)
    total = len(terms)
    return {
        term: (count / total) * idf.get(term, 0.0)
        for term, count in counts.items()
    }


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    max_value = max(values)
    if max_value <= 0.0:
        return [0.0 for _ in values]
    return [value / max_value for value in values]


def _frontmatter_line(text: str) -> bool:
    return text == "---" or bool(re.match(r"^[a-zA-Z_-]+:\s*", text))
