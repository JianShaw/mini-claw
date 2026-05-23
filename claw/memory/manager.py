"""记忆生命周期管理器。

这一版先使用确定性规则生成 daily memory 和长期候选，不直接调用 LLM。
这样可以先把“存储路径、更新时机、注入提示词、提炼长期记忆”这条链路
稳定下来；后续要升级成 LLM extractor/distiller 时，只需要替换渲染和
提炼辅助函数，外部接口可以保持不变。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from claw.memory.embedding import EmbeddingProvider, FastEmbedProvider
from claw.memory.search import HybridMemorySearch, MemoryChunk, build_memory_chunks
from claw.memory.store import DailyMemoryStore, LongTermMemoryStore
from claw.memory.vector_index import (
    MemorySource,
    MemoryVectorIndexError,
    SQLiteMemoryVectorIndex,
)
from claw.types import ChatMessage, InboundMessage, Session

logger = logging.getLogger(__name__)

_DAILY_SECTIONS = [
    "Current Context",
    "Recent User Intent",
    "Active Tasks",
    "Decisions Today",
    "Temporary TODO",
    "Tool Results",
    "Long-Term Candidates",
]

_NOISY_DAILY_TITLES = {"Recent User Intent", "Tool Results"}
_NOISY_DAILY_PREFIXES = (
    "Recent user topic:",
    "Recent assistant response:",
)


@dataclass(slots=True)
class MemoryDistillResult:
    """长期记忆提炼结果。"""

    added: int
    path: Path


class MemoryManager:
    """协调 daily memory、长期记忆和 LLM 提示词注入。"""

    def __init__(
        self,
        root: str | Path = "data/memory",
        *,
        update_every: int = 3,
        max_context_chars: int = 8000,
        memory_top_k: int = 8,
        embedding_provider: EmbeddingProvider | None = None,
        vector_index: SQLiteMemoryVectorIndex | None = None,
        use_vector_index: bool | None = None,
        today_provider: Callable[[], date] | None = None,
        vector_db_path: str | Path | None = None,
    ) -> None:
        self.daily_store = DailyMemoryStore(root)
        self.long_store = LongTermMemoryStore(root)
        self.update_every = update_every
        self.max_context_chars = max_context_chars
        self.memory_top_k = memory_top_k
        self.searcher = HybridMemorySearch()
        self._use_vector_index = (
            use_vector_index
            if use_vector_index is not None
            else os.environ.get("MEMORY_BACKEND", "sqlite").lower() == "sqlite"
        )
        self._vector_warning_logged = False
        self.vector_index = vector_index
        if self._use_vector_index and self.vector_index is None:
            provider = embedding_provider or FastEmbedProvider()
            # 默认由 SQLiteMemoryVectorIndex 自行管理 db_path（root / memory_index.sqlite3）
            # 生产环境通过 vector_db_path 复用主数据库
            self.vector_index = SQLiteMemoryVectorIndex(
                root, provider, db_path=vector_db_path
            )
        self._today_provider = today_provider or date.today

    def today(self) -> date:
        """返回当前记忆日期；测试中可以通过 today_provider 固定日期。"""
        return self._today_provider()

    async def build_context(self, message: InboundMessage | None = None) -> str:
        """构建要注入下一次 LLM 调用的记忆上下文。

        注意：记忆只是背景信息。模板里明确要求 LLM 在冲突时优先相信
        当前用户消息和当前 session history，避免旧记忆压过新指令。
        """
        long_memory = self.long_store.read().strip()
        daily_memory = self.daily_store.read(self.today()).strip()
        if not long_memory and not daily_memory:
            return ""

        query = message.text if message is not None else ""
        if self._use_vector_index and self.vector_index is not None:
            selected = self._search_vector_memory(query, long_memory, daily_memory)
            if selected is not None:
                return self._render_context(selected)

        selected = self._search_hybrid_memory(query, long_memory, daily_memory)
        return self._render_context(selected)

    def _search_vector_memory(
        self,
        query: str,
        long_memory: str,
        daily_memory: str,
    ) -> list[MemoryChunk] | None:
        """Search the derived SQLite vector index; return None on fallback."""
        if self.vector_index is None:
            return None
        sources: list[MemorySource] = []
        if long_memory:
            sources.append(MemorySource(
                path=self.long_store.path,
                source="Long-Term Memory",
                markdown=long_memory,
            ))
        if daily_memory:
            sources.append(MemorySource(
                path=self.daily_store.path_for(self.today()),
                source="Today's Daily Memory",
                markdown=daily_memory,
            ))
        try:
            results = self.vector_index.search(
                query,
                sources,
                top_k=max(self.memory_top_k * 3, self.memory_top_k),
            )
        except MemoryVectorIndexError as exc:
            if not self._vector_warning_logged:
                logger.warning(
                    "memory vector search unavailable; falling back to hybrid search: %s",
                    exc,
                )
                self._vector_warning_logged = True
            return None

        filtered = _filter_retrieval_chunks(
            [result.chunk for result in results],
            query,
        )
        selected = filtered[:self.memory_top_k]
        logger.debug(
            "memory vector retrieval query=%r results=%d filtered=%d top_k=%d",
            query,
            len(results),
            len(filtered),
            self.memory_top_k,
        )
        for idx, result in enumerate(results[: self.memory_top_k], start=1):
            logger.debug(
                "memory vector result #%d source=%s title=%s score=%.3f text=%r",
                idx,
                result.chunk.source,
                result.chunk.title,
                result.score,
                result.chunk.text[:120],
            )
        return selected

    def _search_hybrid_memory(
        self,
        query: str,
        long_memory: str,
        daily_memory: str,
    ) -> list[MemoryChunk]:
        raw_chunks = build_memory_chunks(long_memory, daily_memory)
        chunks = _filter_retrieval_chunks(raw_chunks, query)
        results = self.searcher.search(query, chunks, top_k=self.memory_top_k)
        logger.debug(
            "memory retrieval query=%r chunks=%d filtered=%d top_k=%d results=%d",
            query,
            len(raw_chunks),
            len(chunks),
            self.memory_top_k,
            len(results),
        )
        for idx, result in enumerate(results, start=1):
            logger.debug(
                (
                    "memory result #%d source=%s title=%s score=%.3f "
                    "semantic=%.3f bm25=%.3f text=%r"
                ),
                idx,
                result.chunk.source,
                result.chunk.title,
                result.score,
                result.semantic_score,
                result.bm25_score,
                result.chunk.text[:120],
            )
        return [result.chunk for result in results]

    def _render_context(self, selected: list[MemoryChunk]) -> str:
        long_items = [
            chunk.text
            for chunk in selected
            if chunk.source == "Long-Term Memory"
        ]
        daily_items = [
            chunk.text
            for chunk in selected
            if chunk.source == "Today's Daily Memory"
        ]

        long_context = "\n".join(f"- {item}" for item in _unique(long_items))
        daily_context = "\n".join(f"- {item}" for item in _unique(daily_items))

        context = (
            "[Memory Context]\n\n"
            "Use this memory only as background context.\n"
            "The current user message and current session history have higher priority.\n"
            "If memory is outdated or conflicts with current instructions, follow the current conversation.\n\n"
            "[Retrieval]\n"
            "Memory search uses SQLite vector retrieval when available, with hybrid lexical fallback.\n\n"
            "[Long-Term Memory]\n"
            f"{long_context or '(empty)'}\n\n"
            "[Today's Daily Memory]\n"
            f"{daily_context or '(empty)'}"
        )
        clipped = _clip(context, self.max_context_chars)
        logger.debug(
            "memory context rendered chars=%d clipped_chars=%d",
            len(context),
            len(clipped),
        )
        return clipped

    async def maybe_update_daily(self, session: Session, *, force: bool = False) -> bool:
        """按策略更新当天 daily memory。

        默认策略是每累计 ``update_every`` 轮用户消息更新一次；compact
        或手动命令会传入 force=True 强制更新。
        """
        user_count = sum(1 for msg in session.history if msg.role == "user")
        if user_count == 0 and not session.summary:
            return False

        last_count = int(session.metadata.get("_memory_last_daily_user_count", 0))
        # 避免同一个 user_count 被重复写入；比如保存后又再次调用 update。
        should_update = force or (
            self.update_every > 0
            and user_count > 0
            and user_count % self.update_every == 0
            and user_count != last_count
        )
        if not should_update:
            return False

        content = self._render_daily_memory(session)
        self.daily_store.write(self.today(), content)
        session.metadata["_memory_last_daily_user_count"] = user_count
        return True

    async def force_update_daily(self, session: Session) -> bool:
        """强制用当前 session 状态重写当天 daily memory。"""
        return await self.maybe_update_daily(session, force=True)

    async def distill_daily_to_long_term(self, day: date | None = None) -> MemoryDistillResult:
        """把 daily memory 的长期候选提炼进 MEMORY.md。

        第一版只做保守去重和合并：读取 ``Long-Term Candidates`` 区块，
        将尚未存在的 bullet 追加到长期记忆。后续可以替换成 LLM 判断
        “是否稳定、是否需要用户确认、应该归入哪个分类”。
        """
        day = day or self.today()
        daily = self.daily_store.read(day)
        candidates = _extract_section_bullets(daily, "Long-Term Candidates")
        existing = self.long_store.read()
        existing_bullets = set(_all_bullets(existing))
        new_items = [item for item in candidates if item not in existing_bullets]

        if not existing.strip():
            existing = "# Memory\n\n## Consolidated Memory\n"
        elif "## Consolidated Memory" not in existing:
            existing = existing.rstrip() + "\n\n## Consolidated Memory\n"

        if new_items:
            existing = existing.rstrip() + "\n" + "\n".join(f"- {item}" for item in new_items) + "\n"
            self.long_store.write(existing)
        elif not self.long_store.path.exists():
            self.long_store.write(existing.rstrip() + "\n")

        return MemoryDistillResult(added=len(new_items), path=self.long_store.path)

    def _render_daily_memory(self, session: Session) -> str:
        """把当前 session 的近期状态渲染成 daily memory Markdown。

        这里不是保存完整聊天记录，而是把近期上下文、意图、TODO、工具结果
        和长期候选整理成固定区块，方便人读，也方便后续 distill。
        """
        day = self.today()
        now = datetime.now().isoformat(timespec="seconds")
        recent = session.history[-12:]
        users = [m.content for m in recent if m.role == "user"]
        assistants = [m.content for m in recent if m.role == "assistant" and m.content]
        tools = [m for m in recent if m.role == "tool"]

        current_context = []
        if session.summary:
            current_context.append(f"Session summary: {_one_line(session.summary)}")
        if users:
            current_context.append(f"Recent user topic: {_one_line(users[-1])}")
        if assistants:
            current_context.append(f"Recent assistant response: {_one_line(assistants[-1])}")

        recent_intent: list[str] = [_one_line(text) for text in users[-5:]]
        tool_results = [
            f"{msg.tool_name or 'tool'}: {_one_line(msg.content)}"
            for msg in tools[-5:]
        ]
        candidates = _candidate_items(session.history)

        sections = {
            "Current Context": current_context,
            "Recent User Intent": recent_intent,
            "Active Tasks": _task_items(users),
            "Decisions Today": _decision_items(users),
            "Temporary TODO": _todo_items(users),
            "Tool Results": tool_results,
            "Long-Term Candidates": candidates,
        }

        lines = [
            "---",
            f"date: {day.isoformat()}",
            "type: daily_memory",
            f"updated_at: {now}",
            "---",
            "",
            f"# {day.isoformat()} Daily Memory",
            "",
        ]
        for section in _DAILY_SECTIONS:
            lines.append(f"## {section}")
            items = sections[section]
            if items:
                lines.extend(f"- {item}" for item in _unique(items))
            else:
                lines.append("- None recorded.")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _one_line(text: str, limit: int = 180) -> str:
    """把多行内容压成单行，避免 daily memory 被长消息撑爆。"""
    return _clip(" ".join(text.split()), limit)


def _filter_retrieval_chunks(chunks: list[MemoryChunk], query: str) -> list[MemoryChunk]:
    """Drop daily-memory echoes that duplicate current/session context."""
    normalized_query = _normalize_retrieval_text(query)
    filtered: list[MemoryChunk] = []
    for chunk in chunks:
        if chunk.source == "Today's Daily Memory":
            text = chunk.text.strip()
            if chunk.title in _NOISY_DAILY_TITLES:
                continue
            if any(text.startswith(prefix) for prefix in _NOISY_DAILY_PREFIXES):
                continue
            if normalized_query and _normalize_retrieval_text(text) == normalized_query:
                continue
        filtered.append(chunk)
    return filtered


def _normalize_retrieval_text(text: str) -> str:
    return " ".join(text.split()).strip().rstrip("?!?!.。？")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _candidate_items(history: list[ChatMessage]) -> list[str]:
    """从用户消息里提取可能值得长期保存的候选。

    这是确定性 MVP 规则：只要用户表达偏好、决定、长期目标等信号，就先
    放进 daily memory 的候选区。真正写入长期记忆时还会做去重。
    """
    items: list[str] = []
    keywords = (
        "prefer", "preference", "want", "decide", "decision", "should",
        "希望", "偏好", "决定", "长期", "以后", "记住", "就这么",
    )
    for msg in history:
        if msg.role != "user":
            continue
        text = _one_line(msg.content)
        if any(keyword in text for keyword in keywords):
            items.append(text)
    return items[-8:]


def _task_items(user_messages: list[str]) -> list[str]:
    """提取当天仍然和任务推进有关的用户消息。"""
    items = []
    for text in user_messages[-5:]:
        line = _one_line(text)
        if any(k in line for k in ("实现", "设计", "改", "add", "implement", "build")):
            items.append(line)
    return items


def _decision_items(user_messages: list[str]) -> list[str]:
    """提取当天做出的显式决策。"""
    items = []
    for text in user_messages[-5:]:
        line = _one_line(text)
        if any(k in line for k in ("就这么", "决定", "挺好", "用这个", "定", "choose")):
            items.append(line)
    return items


def _todo_items(user_messages: list[str]) -> list[str]:
    """提取临时 TODO 或后续事项。"""
    items = []
    for text in user_messages[-5:]:
        line = _one_line(text)
        if any(k in line for k in ("todo", "TODO", "之后", "后续", "还要", "next")):
            items.append(line)
    return items


def _extract_section_bullets(markdown: str, section: str) -> list[str]:
    """从指定 Markdown 二级标题下读取 bullet 列表。"""
    lines = markdown.splitlines()
    in_section = False
    bullets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped == f"## {section}"
            continue
        if in_section and stripped.startswith("- "):
            item = stripped[2:].strip()
            if item and item != "None recorded.":
                bullets.append(item)
    return _unique(bullets)


def _all_bullets(markdown: str) -> list[str]:
    """读取整份 Markdown 中所有 bullet，用于长期记忆去重。"""
    return [
        line.strip()[2:].strip()
        for line in markdown.splitlines()
        if line.strip().startswith("- ")
    ]
