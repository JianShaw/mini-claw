"""专家注册表：查询/搜索/分类接口。

轻量封装 SqliteExpertStore，提供按分类、标签、关键词的查询能力。
"""

from __future__ import annotations

from claw.expert.store import SqliteExpertStore
from claw.expert.types import Expert


class ExpertRegistry:
    """专家查询注册表。"""

    def __init__(self, store: SqliteExpertStore) -> None:
        self._store = store

    def get(self, name: str) -> Expert | None:
        """按名称获取专家。"""
        return self._store.get(name)

    def list_all(self) -> list[Expert]:
        """列出所有专家。"""
        return self._store.list_all()

    def list_by_category(self, category: str) -> list[Expert]:
        """按分类列出专家。"""
        return [e for e in self._store.list_all() if e.meta.category == category]

    def search(self, query: str) -> list[Expert]:
        """搜索专家（名称 + display_name + description 模糊匹配）。"""
        q = query.lower()
        return [
            e for e in self._store.list_all()
            if q in e.name.lower()
            or q in e.display_name.lower()
            or q in e.description.lower()
        ]

    def list_by_tag(self, tag: str) -> list[Expert]:
        """按标签列出专家。"""
        return [e for e in self._store.list_all() if tag in e.meta.tags]

    def list_categories(self) -> list[str]:
        """列出所有分类。"""
        categories = {e.meta.category for e in self._store.list_all() if e.meta.category}
        return sorted(categories)
