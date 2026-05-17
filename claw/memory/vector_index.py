"""SQLite-backed derived vector index for markdown memory.

双模式架构：
  - sqlite-vec 可用时：vec0 虚表 + MATCH KNN 查询，距离计算在 C 扩展层完成
  - sqlite-vec 不可用时：JSON embedding 列 + Python cosine 暴力排序（fallback）

对外接口 ``search()`` 签名和返回值在两种模式下完全一致。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from claw.memory.embedding import EmbeddingProvider
from claw.memory.search import MemoryChunk, build_memory_chunks

logger = logging.getLogger(__name__)


class MemoryVectorIndexError(RuntimeError):
    """Raised when the derived vector index cannot be used."""


@dataclass(slots=True)
class MemorySource:
    path: Path
    source: str
    markdown: str


@dataclass(slots=True)
class VectorMemorySearchResult:
    chunk: MemoryChunk
    score: float


def serialize_f32(vector: list[float]) -> bytes:
    """将 float 列表序列化为 sqlite-vec 所需的 bytes 格式。"""
    return struct.pack("%sf" % len(vector), *vector)


class SQLiteMemoryVectorIndex:
    """A rebuildable SQLite index derived from markdown memory files."""

    def __init__(
        self,
        root: str | Path,
        embedding_provider: EmbeddingProvider,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.db_path = Path(db_path) if db_path is not None else self.root / "memory_index.sqlite3"
        self.embedding_provider = embedding_provider
        self._dim: int | None = None
        self._vec_available: bool = False
        self._try_load_vec_ext()

    def _try_load_vec_ext(self) -> None:
        """尝试加载 sqlite-vec 扩展，失败则静默降级到 fallback 模式。"""
        try:
            import sqlite_vec
        except ImportError:
            logger.info("sqlite-vec not installed; using Python cosine fallback")
            return

        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.close()
            self._vec_available = True
            logger.info("sqlite-vec extension loaded; vec0 mode enabled")
        except Exception as exc:
            logger.info("sqlite-vec extension load failed: %s; using Python cosine fallback", exc)

    def search(
        self,
        query: str,
        sources: list[MemorySource],
        *,
        top_k: int,
    ) -> list[VectorMemorySearchResult]:
        if top_k <= 0:
            return []
        try:
            with self._connect() as conn:
                self._ensure_schema(conn)
                self._sync_sources(conn, sources)
                if self._vec_available:
                    return self._search_vec0(conn, query, sources, top_k)
                return self._search_fallback(conn, query, sources, top_k)
        except Exception as exc:
            raise MemoryVectorIndexError(str(exc)) from exc

    # ------------------------------------------------------------------
    # vec0 模式：KNN 查询
    # ------------------------------------------------------------------

    def _search_vec0(
        self,
        conn: sqlite3.Connection,
        query: str,
        sources: list[MemorySource],
        top_k: int,
    ) -> list[VectorMemorySearchResult]:
        source_paths = [str(source.path) for source in sources]
        rows = self._load_meta_chunks(conn, source_paths)
        if not rows:
            return []
        if not query.strip():
            return [
                VectorMemorySearchResult(
                    chunk=MemoryChunk(row["source"], row["title"], row["text"]),
                    score=0.0,
                )
                for row in rows[:top_k]
            ]

        query_vector = self.embedding_provider.embed([query])[0]
        knn_k = max(top_k * 3, top_k)
        placeholders = ",".join("?" for _ in source_paths)
        vec_rows = list(conn.execute(
            f"""
            SELECT v.rowid, v.distance
            FROM vec_memory_chunks v
            WHERE v.embedding MATCH ? AND v.k = ?
            ORDER BY v.distance
            """,
            [serialize_f32(query_vector), knn_k],
        ))

        if not vec_rows:
            return []

        # 过滤只保留目标 source_path 的结果
        rowid_to_meta = {row["id"]: row for row in rows}
        matched_ids = {row[0] for row in vec_rows if row[0] in rowid_to_meta}
        placeholders = ",".join("?" for _ in matched_ids)
        meta_rows = {
            row["id"]: row
            for row in conn.execute(
                f"""
                SELECT id, scope AS source, title, text
                FROM memory_chunks
                WHERE id IN ({placeholders}) AND source_path IN ({','.join('?' for _ in source_paths)})
                """,
                list(matched_ids) + source_paths,
            )
        }

        dist_map = {row[0]: row[1] for row in vec_rows}
        results: list[VectorMemorySearchResult] = []
        for rowid, distance in vec_rows:
            meta = meta_rows.get(rowid)
            if meta is None:
                continue
            score = 1.0 - distance
            if score <= 0.0:
                continue
            results.append(VectorMemorySearchResult(
                chunk=MemoryChunk(meta["source"], meta["title"], meta["text"]),
                score=score,
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # fallback 模式：Python cosine 暴力排序
    # ------------------------------------------------------------------

    def _search_fallback(
        self,
        conn: sqlite3.Connection,
        query: str,
        sources: list[MemorySource],
        top_k: int,
    ) -> list[VectorMemorySearchResult]:
        rows = self._load_chunks(conn, [source.path for source in sources])
        if not rows:
            return []
        if not query.strip():
            return [
                VectorMemorySearchResult(
                    chunk=MemoryChunk(row["source"], row["title"], row["text"]),
                    score=0.0,
                )
                for row in rows[:top_k]
            ]
        query_vector = self.embedding_provider.embed([query])[0]
        scored = [
            (_cosine(query_vector, json.loads(row["embedding"])), row)
            for row in rows
        ]
        results = [
            VectorMemorySearchResult(
                chunk=MemoryChunk(row["source"], row["title"], row["text"]),
                score=score,
            )
            for score, row in scored
            if score > 0.0
        ]
        results.sort(key=lambda result: result.score, reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # 连接与 schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if self._vec_available:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_sources (
                source_path TEXT PRIMARY KEY,
                source_mtime REAL NOT NULL,
                source_hash TEXT NOT NULL
            )
        """)
        if self._vec_available:
            self._ensure_vec0_schema(conn)
        else:
            self._ensure_fallback_schema(conn)
        conn.commit()

    def _ensure_vec0_schema(self, conn: sqlite3.Connection) -> None:
        """vec0 模式的 schema：元数据表。vec0 虚表在首次拿到 embedding 后按需创建。"""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                scope TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_chunks_source_path
            ON memory_chunks(source_path)
        """)

    def _ensure_fallback_schema(self, conn: sqlite3.Connection) -> None:
        """fallback 模式的 schema：带 embedding TEXT 列的宽表。"""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                source_mtime REAL NOT NULL,
                source_hash TEXT NOT NULL,
                scope TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_chunks_source_path
            ON memory_chunks(source_path)
        """)

    def _ensure_vec0_table(self, conn: sqlite3.Connection) -> None:
        """根据已知的 _dim 创建 vec0 虚表（幂等）。"""
        if self._dim is None or not self._vec_available:
            return
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory_chunks USING vec0(
                embedding float[{self._dim}] distance_metric=cosine
            )
        """)

    # ------------------------------------------------------------------
    # 数据同步
    # ------------------------------------------------------------------

    def _sync_sources(self, conn: sqlite3.Connection, sources: list[MemorySource]) -> None:
        active_paths = {str(source.path) for source in sources}
        existing_paths = {
            row["source_path"]
            for row in conn.execute("SELECT source_path FROM memory_sources")
        }
        for stale_path in existing_paths - active_paths:
            self._delete_chunks_for_source(conn, stale_path)
            conn.execute("DELETE FROM memory_sources WHERE source_path = ?", (stale_path,))

        for source in sources:
            source_path = str(source.path)
            source_hash = hashlib.sha256(source.markdown.encode("utf-8")).hexdigest()
            source_mtime = source.path.stat().st_mtime if source.path.exists() else 0.0
            row = conn.execute(
                "SELECT source_mtime, source_hash FROM memory_sources WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            if row and row["source_hash"] == source_hash:
                continue
            self._delete_chunks_for_source(conn, source_path)
            chunks = _chunks_for_source(source.source, source.markdown)
            if not chunks:
                continue
            embeddings = self.embedding_provider.embed([
                f"{chunk.source} {chunk.title} {chunk.text}"
                for chunk in chunks
            ])
            # 检测维度并按需创建 vec0 虚表
            if self._dim is None and embeddings:
                self._dim = len(embeddings[0])
                self._ensure_vec0_table(conn)
            if self._vec_available:
                self._insert_vec0_chunks(conn, source_path, source_hash, chunks, embeddings)
            else:
                self._insert_fallback_chunks(conn, source_path, source_mtime, source_hash, chunks, embeddings)
            conn.execute(
                """
                INSERT INTO memory_sources(source_path, source_mtime, source_hash)
                VALUES (?, ?, ?)
                ON CONFLICT(source_path) DO UPDATE SET
                    source_mtime = excluded.source_mtime,
                    source_hash = excluded.source_hash
                """,
                (source_path, source_mtime, source_hash),
            )
        conn.commit()

    def _delete_chunks_for_source(self, conn: sqlite3.Connection, source_path: str) -> None:
        """删除某个 source 的所有 chunks，vec0 模式同时清理虚表。"""
        if self._vec_available:
            chunk_ids = [
                row[0] for row in conn.execute(
                    "SELECT id FROM memory_chunks WHERE source_path = ?", (source_path,)
                )
            ]
            if chunk_ids:
                placeholders = ",".join("?" for _ in chunk_ids)
                conn.execute(f"DELETE FROM vec_memory_chunks WHERE rowid IN ({placeholders})", chunk_ids)
        conn.execute("DELETE FROM memory_chunks WHERE source_path = ?", (source_path,))

    def _insert_vec0_chunks(
        self,
        conn: sqlite3.Connection,
        source_path: str,
        source_hash: str,
        chunks: list[MemoryChunk],
        embeddings: list[list[float]],
    ) -> None:
        """vec0 模式：向元数据表 + vec0 虚表插入 chunks。"""
        for chunk, embedding in zip(chunks, embeddings):
            cursor = conn.execute(
                """
                INSERT INTO memory_chunks (source_path, source_hash, scope, title, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_path, source_hash, chunk.source, chunk.title, chunk.text),
            )
            chunk_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO vec_memory_chunks(rowid, embedding) VALUES (?, ?)",
                [chunk_id, serialize_f32(embedding)],
            )

    def _insert_fallback_chunks(
        self,
        conn: sqlite3.Connection,
        source_path: str,
        source_mtime: float,
        source_hash: str,
        chunks: list[MemoryChunk],
        embeddings: list[list[float]],
    ) -> None:
        """fallback 模式：向带 embedding TEXT 列的宽表插入 chunks。"""
        for chunk, embedding in zip(chunks, embeddings):
            conn.execute(
                """
                INSERT INTO memory_chunks (
                    source_path, source_mtime, source_hash, scope, title, text, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_path,
                    source_mtime,
                    source_hash,
                    chunk.source,
                    chunk.title,
                    chunk.text,
                    json.dumps(embedding),
                ),
            )

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def _load_chunks(
        self,
        conn: sqlite3.Connection,
        source_paths: list[Path],
    ) -> list[sqlite3.Row]:
        """fallback 模式：加载含 embedding 的 chunks。"""
        if not source_paths:
            return []
        placeholders = ",".join("?" for _ in source_paths)
        return list(conn.execute(
            f"""
            SELECT scope AS source, title, text, embedding
            FROM memory_chunks
            WHERE source_path IN ({placeholders})
            ORDER BY id
            """,
            [str(path) for path in source_paths],
        ))

    def _load_meta_chunks(
        self,
        conn: sqlite3.Connection,
        source_paths: list[str],
    ) -> list[sqlite3.Row]:
        """vec0 模式：加载不含 embedding 的元数据 chunks。"""
        if not source_paths:
            return []
        placeholders = ",".join("?" for _ in source_paths)
        return list(conn.execute(
            f"""
            SELECT id, scope AS source, title, text
            FROM memory_chunks
            WHERE source_path IN ({placeholders})
            ORDER BY id
            """,
            source_paths,
        ))


def _chunks_for_source(source: str, markdown: str) -> list[MemoryChunk]:
    if source == "Long-Term Memory":
        return build_memory_chunks(markdown, "")
    if source == "Today's Daily Memory":
        return build_memory_chunks("", markdown)
    return []


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
