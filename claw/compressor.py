"""上下文压缩器：检测 session 是否超过 token 阈值，自动压缩旧消息保留最近 N 轮。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from claw.tokens import estimate_session_tokens, estimate_tokens

if TYPE_CHECKING:
    from claw.types import Session

logger = logging.getLogger(__name__)

_COMPACT_SYSTEM_PROMPT = (
    "你是一个对话摘要助手。请用简洁的中文总结以下对话的关键信息，"
    "保留重要的事实、决策和上下文。不要遗漏用户明确提出的偏好和要求。"
    "只输出摘要内容，不要添加额外说明。"
)


class ContextCompressor:
    """自动上下文压缩器。

    检测 session 是否超过 token 阈值，超过时将旧消息压缩为摘要，
    保留最近 keep_rounds 轮对话不变。
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        max_tokens: int = 8000,
        keep_rounds: int = 4,
        enabled: bool = True,
    ) -> None:
        self._client = client
        self._model = model
        self.max_tokens = max_tokens
        self.keep_rounds = keep_rounds
        self.enabled = enabled

    def should_compress(self, session: Session, incoming_text: str | None = None) -> bool:
        """判断 session 是否需要压缩。

        enabled=False 时始终返回 False（不影响手动 /compact）。
        消息不足 keep_rounds*2+2 时不压缩。
        上次压缩后新积累不足 keep_rounds*2 条时不压缩（防止连续触发）。
        incoming_text 计入总量以避免追加后立即超阈值。
        """
        if not self.enabled:
            return False
        min_messages = self.keep_rounds * 2 + 2
        if len(session.history) < min_messages:
            return False
        # 冷却：压缩后至少再积累 keep_rounds*2 条新消息
        last_len = session.metadata.get("_compact_cooldown", 0)
        new_since_compress = len(session.history) - last_len
        if new_since_compress <= self.keep_rounds * 2:
            return False
        total = estimate_session_tokens(session, extra_text=incoming_text)
        return total > self.max_tokens

    async def compress(self, session: Session, *, force: bool = False) -> str | None:
        """压缩 session 的旧消息，保留最近 keep_rounds 轮。

        force=True 时绕过 enabled 和阈值检查（手动 /compact 场景），
        但仍遵守结构性边界（split_point == 0 时不压缩）。

        成功时原地修改 session.summary / session.history / session.history_offset，
        返回新摘要文本。失败时 session 不变，返回 None。
        """
        split_point = self._find_split_point(session)
        if split_point == 0:
            return None

        older = session.history[:split_point]
        recent = session.history[split_point:]

        new_summary = await self._generate_summary(older, session.summary)
        if new_summary is None:
            return None

        # LLM 成功后才修改 session，确保失败时不留脏状态
        session.history_offset += split_point
        session.summary = new_summary
        session.history = recent
        # 记录压缩后的 history 长度，作为下次压缩的冷却起点
        session.metadata["_compact_cooldown"] = len(recent)

        logger.info(
            "Context compressed: %d older messages summarized, "
            "%d recent messages kept, summary length=%d",
            len(older), len(recent), len(new_summary),
        )
        return new_summary

    def _find_split_point(self, session: Session) -> int:
        """定位 history 的分割点：保留最近 keep_rounds 轮，更早的部分压缩。

        一轮 = 一个 user + 一个 assistant 消息对。
        从末尾倒数 assistant 消息来定位 keep_rounds 的边界。

        Split invariant: recent 部分必须从 user 消息开始。
        若 history 不足 keep_rounds 轮或全部在 keep 范围内，返回 0。
        """
        history = session.history
        total = len(history)

        # 从末尾找第 keep_rounds 个 assistant
        rounds_found = 0
        last_assistant_idx = -1
        for i in range(total - 1, -1, -1):
            if history[i].role == "assistant":
                rounds_found += 1
                if rounds_found == self.keep_rounds:
                    last_assistant_idx = i
                    break

        # 不够 keep_rounds 轮，不压缩
        if rounds_found < self.keep_rounds:
            return 0

        # 从 last_assistant_idx 向前找配对的 user，作为 recent 起点
        split = last_assistant_idx
        while split > 0 and history[split].role != "user":
            split -= 1

        # 找不到 user 或 split=0（全部在 keep 范围内），不压缩
        if history[split].role != "user" or split == 0:
            return 0

        return split

    async def _generate_summary(
        self, messages: list, existing_summary: str | None
    ) -> str | None:
        """调用 LLM 生成摘要。失败时返回 None。"""
        history_text = "\n".join(
            f"{m.role}: {m.content}" for m in messages
        )
        existing = f"已有摘要：\n{existing_summary}\n\n" if existing_summary else ""
        user_content = (
            f"{existing}请总结以下对话的关键信息，保留重要的事实和上下文：\n\n"
            f"{history_text}"
        )

        api_messages = [
            {"role": "system", "content": _COMPACT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=api_messages,
                temperature=0.3,
                max_tokens=1000,
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.exception("Failed to generate compression summary")
            return None
