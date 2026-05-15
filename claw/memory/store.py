"""基于 Markdown 文件的记忆存储。

当前只支持本地单用户场景，因此路径固定为：

* daily/短期记忆：``data/memory/daily/YYYY-MM-DD.md``
* 长期记忆：``data/memory/MEMORY.md``
"""

from __future__ import annotations

from datetime import date
from pathlib import Path


class DailyMemoryStore:
    """按日期保存短期记忆。

    daily memory 是“当天工作笔记”，不是 session 原始记录；它会被
    MemoryManager 按策略重写成结构化 Markdown。
    """

    def __init__(self, root: str | Path = "data/memory") -> None:
        self.root = Path(root)

    def path_for(self, day: date) -> Path:
        """返回某一天的 daily memory 路径。"""
        return self.root / "daily" / f"{day.isoformat()}.md"

    def read(self, day: date) -> str:
        path = self.path_for(day)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write(self, day: date, content: str) -> None:
        path = self.path_for(day)
        _atomic_write(path, content)


class LongTermMemoryStore:
    """保存长期记忆。

    长期记忆用于跨 session 注入 LLM，内容应该是稳定偏好、项目事实、
    已确认决策等，而不是当天临时上下文。
    """

    def __init__(self, root: str | Path = "data/memory") -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / "MEMORY.md"

    def read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def write(self, content: str) -> None:
        _atomic_write(self.path, content)


def _atomic_write(path: Path, content: str) -> None:
    """原子写入 Markdown，避免写到一半时留下损坏文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
