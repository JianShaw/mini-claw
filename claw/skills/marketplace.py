"""本地技能市场操作：安装、卸载、导入导出。

支持 SKILL.md 格式（YAML frontmatter + Markdown body），对齐 OpenClaw 技能格式。
每个技能存储为独立目录，包含一个 SKILL.md 文件。
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claw.skills.loader import SkillLoader
from claw.skills.registry import SkillsRegistry
from claw.skills.store import SkillStore
from claw.skills.types import Skill

_SKILL_FILE = "SKILL.md"

logger = logging.getLogger(__name__)


class MarketplaceOps:
    """本地技能市场操作：安装、卸载、导出。

    技能源：
    - 本地文件系统路径（SKILL.md 文件或包含 SKILL.md 的目录）
    - ZIP 压缩包（包含一个或多个技能目录）
    - 未来可扩展：远程 URL / ClawHub registry
    """

    def __init__(self, store: SkillStore, registry: SkillsRegistry) -> None:
        self._store = store
        self._registry = registry
        self._loader = SkillLoader()

    def install_from_file(self, path: str | Path) -> Skill:
        """从单个 SKILL.md 文件安装技能。"""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        skill = self._loader.load_file(file_path)
        skill.source = "local"
        self._store.save(skill)
        # 如果已注册，更新；否则注册
        if self._registry.get(skill.name):
            self._registry.update(skill)
        else:
            self._registry.register(skill)
        return skill

    def install_from_zip(self, zip_path: str | Path) -> list[Skill]:
        """从 ZIP 压缩包安装技能。

        ZIP 中可包含 SKILL.md 文件或包含 SKILL.md 的技能目录。
        """
        archive = Path(zip_path)
        if not archive.exists():
            raise FileNotFoundError(f"ZIP 文件不存在: {archive}")

        # ZIP 内单个条目的最大尺寸限制（1MB）
        _MAX_ENTRY_SIZE = 1 * 1024 * 1024

        installed: list[Skill] = []
        with zipfile.ZipFile(archive, "r") as zf:
            # 查找 ZIP 中的 SKILL.md 文件
            skill_files = [n for n in zf.namelist() if n.endswith(_SKILL_FILE)]
            if not skill_files:
                raise ValueError(f"ZIP 中没有 {_SKILL_FILE} 技能文件: {archive}")

            for name in skill_files:
                try:
                    # 安全校验：文件大小限制
                    info = zf.getinfo(name)
                    if info.file_size > _MAX_ENTRY_SIZE:
                        logger.warning("跳过超大文件 %s (%d bytes)", name, info.file_size)
                        continue

                    # 提取到临时目录后加载
                    tmp_dir = self._store.root / "_tmp_install"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    # 安全校验：提取纯文件名，防止路径遍历
                    safe_name = Path(name).name
                    if safe_name != _SKILL_FILE:
                        continue

                    # 创建临时技能目录结构
                    skill_tmp_dir = tmp_dir / f"skill_{len(installed)}"
                    skill_tmp_dir.mkdir(parents=True, exist_ok=True)
                    tmp_file = skill_tmp_dir / _SKILL_FILE
                    tmp_file.write_bytes(zf.read(name))

                    skill = self._loader.load_file(tmp_file)
                    skill.source = "local"
                    self._store.save(skill)
                    if self._registry.get(skill.name):
                        self._registry.update(skill)
                    else:
                        self._registry.register(skill)
                    installed.append(skill)
                except Exception as e:
                    logger.warning("跳过 ZIP 中的无效技能 %s: %s", name, e)

            # 清理临时目录
            tmp_dir = self._store.root / "_tmp_install"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return installed

    def install_from_directory(self, dir_path: str | Path) -> list[Skill]:
        """从目录批量安装技能。

        dir_path 可以是：
        - 包含多个技能子目录的父目录（每个子目录有 SKILL.md）
        - 单个技能目录（直接包含 SKILL.md）
        """
        directory = Path(dir_path)
        if not directory.is_dir():
            raise NotADirectoryError(f"不是目录: {directory}")

        # 如果是单个技能目录（直接包含 SKILL.md）
        if (directory / _SKILL_FILE).exists():
            skill = self._loader.load_file(directory / _SKILL_FILE)
            skill.source = "local"
            self._store.save(skill)
            if self._registry.get(skill.name):
                self._registry.update(skill)
            else:
                self._registry.register(skill)
            return [skill]

        # 否则作为包含多个技能子目录的父目录
        skills = self._loader.load_dir(directory)
        installed: list[Skill] = []
        for skill in skills:
            skill.source = "local"
            self._store.save(skill)
            if self._registry.get(skill.name):
                self._registry.update(skill)
            else:
                self._registry.register(skill)
            installed.append(skill)
        return installed

    def remove(self, name: str) -> bool:
        """卸载技能：从存储和注册表同时移除。"""
        if self._registry.get(name):
            self._registry.unregister(name)
        return self._store.delete(name)

    def export_skill(self, name: str, dest: str | Path) -> Path:
        """导出单个技能到目标目录（纯读操作，不写回 store）。"""
        skill = self._registry.get(name)
        if skill is None:
            raise KeyError(f"技能不存在: {name}")

        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)
        skill_dir = dest_path / skill.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        out_file = skill_dir / _SKILL_FILE

        # 直接从 skill 对象写入，不修改 store
        skill_copy = Skill(
            name=skill.name,
            description=skill.description,
            instructions=skill.instructions,
            tools=skill.tools,
            meta=skill.meta,
            source="local",
        )
        from claw.skills.store import SkillStore
        export_store = SkillStore(root=dest_path)
        export_store._local_dir = dest_path
        export_store._skill_to_file(skill_copy, out_file)
        return out_file

    def export_skills(self, names: list[str], dest: str | Path) -> Path:
        """导出多个技能为 ZIP 压缩包（纯读操作，不写回 store）。"""
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)
        zip_file = dest_path / "skills_export.zip"

        from claw.skills.store import SkillStore
        with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in names:
                skill = self._registry.get(name)
                if skill is None:
                    continue
                # 直接从 skill 对象生成 SKILL.md 内容
                content = SkillStore._skill_to_skill_md_static(skill)
                zf.writestr(f"{name}/{_SKILL_FILE}", content)

        return zip_file

    def info(self, name: str) -> dict[str, Any]:
        """获取技能详细信息。"""
        skill = self._registry.get(name)
        if skill is None:
            return {}
        return {
            "name": skill.name,
            "description": skill.description,
            "tools": skill.tools,
            "meta": {
                "version": skill.meta.version,
                "author": skill.meta.author,
                "tags": skill.meta.tags,
                "category": skill.meta.category,
            },
            "source": skill.source,
            "path": skill.path,
        }

    def list_installed(self) -> list[dict[str, Any]]:
        """列出所有已安装技能的摘要信息。"""
        return [
            {
                "name": s.name,
                "description": s.description[:80],
                "source": s.source,
                "version": s.meta.version,
            }
            for s in self._registry.list()
        ]
