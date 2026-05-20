"""专家广场：安装/卸载操作入口。

封装 ExpertService，提供更高层的 marketplace 操作。
未来可扩展 marketplace 源（远程仓库、社区共享等）。
"""

from __future__ import annotations

from pathlib import Path

from claw.expert.service import ExpertService
from claw.expert.store import SqliteExpertStore
from claw.expert.types import Expert


class ExpertMarketplace:
    """专家广场：安装/卸载操作入口。"""

    def __init__(self, store: SqliteExpertStore) -> None:
        self._service = ExpertService(store)

    def list_available(self, *, q: str = "", tag: str = "") -> list[Expert]:
        """列出可用专家，支持关键词和标签过滤。"""
        return self._service.list_experts(q=q, tag=tag)

    def get_detail(self, name: str) -> Expert | None:
        """获取专家详情。"""
        return self._service.get(name)

    def install_bundled(self, name: str) -> Expert:
        """安装 bundled 专家。"""
        return self._service.install_bundled(name)

    def install_from_file(self, file_path: str | Path) -> Expert:
        """从 EXPERT.md 文件安装专家。"""
        return self._service.install_from_file(file_path)

    def uninstall(self, name: str) -> None:
        """卸载本地专家。"""
        self._service.uninstall(name)
