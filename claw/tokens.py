"""Token 估算：基于字符数的零依赖 heuristic，用于判断上下文是否需要压缩。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claw.types import Session

# CJK 字符范围：中日韩统一表意文字、日文假名、韩文
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数。

    启发式规则：拉丁文 ~4 字符/token，CJK ~1.5 字符/token。
    混合内容按字符类型分别计数后求和。
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    latin_len = len(text) - cjk_count
    return max(1, int(cjk_count / 1.5 + latin_len / 4))


def estimate_session_tokens(session: Session, extra_text: str | None = None) -> int:
    """估算 session 总 token 数（summary + 全部 history + 可选的额外文本）。"""
    total = 0
    if session.summary:
        total += estimate_tokens(session.summary)
    for msg in session.history:
        total += estimate_tokens(msg.content)
    if extra_text:
        total += estimate_tokens(extra_text)
    return total
