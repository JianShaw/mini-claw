"""专家服务：CRUD 业务规则，安装/卸载校验。

Web router 和 CLI 都调用此服务，不直接操作 store。
规则包括：名称校验、来源校验（bundled 不可删/不可覆盖）、冲突检查。
"""

from __future__ import annotations

import logging
from pathlib import Path

from claw.expert.store import SqliteExpertStore
from claw.expert.types import Expert

logger = logging.getLogger(__name__)


class ExpertService:
    """专家 CRUD 服务：封装业务规则，供 Web/CLI 调用。"""

    def __init__(self, store: SqliteExpertStore) -> None:
        self._store = store

    def get(self, name: str) -> Expert | None:
        """获取专家详情。"""
        return self._store.get(name)

    def list_experts(
        self, *, q: str = "", tag: str = ""
    ) -> list[Expert]:
        """列出专家，支持名称/描述关键词和标签过滤。"""
        experts = self._store.list_all()
        if q:
            q_lower = q.lower()
            experts = [
                e for e in experts
                if q_lower in e.name.lower()
                or q_lower in e.display_name.lower()
                or q_lower in e.description.lower()
            ]
        if tag:
            experts = [
                e for e in experts
                if tag in e.meta.tags
            ]
        return experts

    def install_bundled(self, name: str) -> Expert:
        """安装指定 bundled 专家到本地（从 bundled 目录导入 SQLite）。

        已安装则返回已有版本（幂等）。
        """
        expert = self._store.get(name)
        if expert is not None:
            return expert

        # 从 bundled 目录查找并导入
        bundled_dir = Path(__file__).parent / "bundled"
        expert_file = bundled_dir / name / "EXPERT.md"
        if not expert_file.exists():
            raise ValueError(f"Bundled 专家不存在: {name}")

        expert = self._store.parse_expert_md_file(expert_file, source="bundled")
        if expert.name != name:
            raise ValueError(
                f"文件内 name '{expert.name}' 与路径名称 '{name}' 不匹配"
            )
        self._store.save(expert)
        logger.info("安装 bundled 专家: %s", name)
        return expert

    def install_from_file(self, file_path: str | Path) -> Expert:
        """从 EXPERT.md 文件安装专家。

        安全校验：
        1. 名称格式校验
        2. bundled 专家不允许覆盖
        """
        path = Path(file_path).resolve()

        expert = self._store.parse_expert_md_file(path, source="local")

        # bundled 专家不允许通过文件安装覆盖
        existing = self._store.get(expert.name)
        if existing is not None and existing.source == "bundled":
            raise ValueError(f"不允许覆盖 bundled 专家: {expert.name}")

        self._store.save(expert)
        logger.info("从文件安装专家: %s (%s)", expert.name, path)
        return expert

    def uninstall(self, name: str) -> None:
        """卸载本地专家。

        规则：
        - bundled 专家不允许卸载
        - 不存在的专家抛 ValueError
        """
        expert = self._store.get(name)
        if expert is None:
            raise ValueError(f"专家不存在: {name}")
        if expert.source == "bundled":
            raise ValueError(f"不允许卸载 bundled 专家: {name}")

        self._store.delete(name)
        logger.info("卸载专家: %s", name)
