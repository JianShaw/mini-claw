"""技能文件存储：管理 SKILL.md 格式的技能文件。

每个技能存储为独立目录，包含一个 SKILL.md 文件（YAML frontmatter + Markdown body），
对齐 OpenClaw 的技能文件格式。

目录结构：
    data/skills/
      index.json                 — 已安装技能索引
      local/                     — 用户安装的技能
        my-skill/
          SKILL.md

    claw/skills/bundled/         — 内置技能（随包分发）
      code-review/
        SKILL.md
      translate/
        SKILL.md
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from claw.skills.types import Skill, SkillLoadError, SkillMeta

logger = logging.getLogger(__name__)

_SKILL_FILE = "SKILL.md"


class SkillStore:
    """技能文件存储，管理 SKILL.md 格式的技能文件。

    职责：
    1. 文件读写 — Skill 对象与 SKILL.md（YAML frontmatter + Markdown body）互转
    2. 目录管理 — bundled（包内只读，claw/skills/bundled/）和 local（用户安装，data/skills/local/）两个源，local 优先
    3. 索引维护 — index.json 记录已安装技能的元信息
    """

    # 包内 bundled 技能目录：claw/skills/bundled/
    _PKG_BUNDLED_DIR = Path(__file__).parent / "bundled"

    def __init__(self, root: str | Path = "data/skills", *, bundled_dir: str | Path | None = None) -> None:
        self._root = Path(root)
        self._bundled_dir = Path(bundled_dir) if bundled_dir else self._PKG_BUNDLED_DIR
        self._local_dir = self._root / "local"
        self._index_path = self._root / "index.json"

    @property
    def root(self) -> Path:
        return self._root

    @property
    def index_path(self) -> Path:
        return self._index_path

    def _ensure_dirs(self) -> None:
        """确保目录存在。"""
        self._bundled_dir.mkdir(parents=True, exist_ok=True)
        self._local_dir.mkdir(parents=True, exist_ok=True)

    def save(self, skill: Skill) -> Path:
        """将技能保存为 SKILL.md 格式，返回文件路径。"""
        self._ensure_dirs()
        target_dir = self._local_dir if skill.source == "local" else self._bundled_dir
        skill_dir = target_dir / skill.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        file_path = skill_dir / _SKILL_FILE

        content = self._skill_to_skill_md(skill)
        file_path.write_text(content, encoding="utf-8")

        skill.path = str(file_path.relative_to(self._root))
        self._update_index(skill)
        return file_path

    def load(self, name: str) -> Skill | None:
        """按名称查找并加载技能，优先 local/ 然后 bundled/。"""
        for dir_path in [self._local_dir, self._bundled_dir]:
            skill_file = dir_path / name / _SKILL_FILE
            if skill_file.exists():
                return self._load_file(skill_file)
        return None

    def delete(self, name: str) -> bool:
        """删除用户安装的技能目录（仅 local/），返回是否成功。

        出于安全考虑，不允许删除 bundled 技能。
        会校验名称合法性并检查路径不越界。
        """
        import shutil

        from claw.skills.types import Skill
        if not Skill.is_valid_name(name):
            return False

        # 仅允许删除 local 目录下的技能，保护 bundled 技能
        skill_dir = (self._local_dir / name).resolve()
        if not skill_dir.is_dir():
            return False
        # 路径包含校验：防止 name 包含 .. 导致越界
        if not str(skill_dir).startswith(str(self._local_dir.resolve())):
            return False

        shutil.rmtree(skill_dir)
        self._remove_from_index(name)
        return True

    def list_files(self) -> list[Path]:
        """列出所有 SKILL.md 文件路径。"""
        files: list[Path] = []
        for dir_path in [self._local_dir, self._bundled_dir]:
            if dir_path.exists():
                files.extend(dir_path.glob(f"*/{_SKILL_FILE}"))
        return sorted(files)

    def load_all(self) -> list[Skill]:
        """加载所有技能，同名时 local/ 覆盖 bundled/。"""
        skills: dict[str, Skill] = {}

        # 先加载 bundled（低优先级）
        if self._bundled_dir.exists():
            for skill_file in sorted(self._bundled_dir.glob(f"*/{_SKILL_FILE}")):
                try:
                    skill = self._load_file(skill_file)
                    skill.source = "bundled"
                    skills[skill.name] = skill
                except SkillLoadError:
                    logger.warning("跳过无效技能文件: %s", skill_file)

        # 再加载 local（高优先级，覆盖同名）
        if self._local_dir.exists():
            for skill_file in sorted(self._local_dir.glob(f"*/{_SKILL_FILE}")):
                try:
                    skill = self._load_file(skill_file)
                    skill.source = "local"
                    skills[skill.name] = skill
                except SkillLoadError:
                    logger.warning("跳过无效技能文件: %s", skill_file)

        return list(skills.values())

    def _load_file(self, path: Path) -> Skill:
        """解析单个 SKILL.md 文件为 Skill 对象。

        SKILL.md 格式：YAML frontmatter（--- 分隔）+ Markdown body（instructions）。
        """
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise SkillLoadError(f"文件读取失败 ({path}): {e}") from e

        data, instructions = self._parse_skill_md(content, path)
        return self._dict_to_skill(data, instructions, path)

    def _parse_skill_md(
        self, content: str, path: Path
    ) -> tuple[dict[str, Any], str]:
        """解析 SKILL.md 内容，返回 (frontmatter_dict, markdown_body)。"""
        if not content.startswith("---"):
            raise SkillLoadError(
                f"SKILL.md 必须以 YAML frontmatter 开头 ({path})"
            )

        # 分离 frontmatter 和 body
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise SkillLoadError(f"SKILL.md frontmatter 格式错误 ({path})")

        frontmatter_text = parts[1]
        body = parts[2].strip()

        try:
            data = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as e:
            raise SkillLoadError(f"YAML 解析失败 ({path}): {e}") from e

        if not isinstance(data, dict):
            raise SkillLoadError(
                f"frontmatter 格式错误 ({path}): 期望字典，得到 {type(data).__name__}"
            )

        return data, body

    def _dict_to_skill(
        self, data: dict[str, Any], instructions: str, path: Path
    ) -> Skill:
        """将 frontmatter 字典 + instructions 文本转换为 Skill。"""
        name = data.get("name", "")
        description = data.get("description", "")

        if not name:
            raise SkillLoadError(f"技能缺少 name 字段 ({path})")
        if not description:
            raise SkillLoadError(f"技能 '{name}' 缺少 description 字段")
        if not instructions:
            raise SkillLoadError(f"技能 '{name}' 缺少 instructions（Markdown body）")
        if not Skill.is_valid_name(name):
            raise SkillLoadError(
                f"技能名称不合法: '{name}'（需要小写字母数字+连字符，1-64字符）"
            )

        # 解析 meta
        meta_data = data.get("meta", {})
        meta = SkillMeta(
            version=str(meta_data.get("version", "1.0.0")),
            author=str(meta_data.get("author", "")),
            tags=meta_data.get("tags", []) or [],
            category=str(meta_data.get("category", "")),
            extra=meta_data.get("extra", {}) or {},
        )

        # 解析 tools
        tools = data.get("tools", []) or []

        return Skill(
            name=name,
            description=description,
            instructions=instructions,
            tools=tools,
            meta=meta,
            source=data.get("source", "local"),
            path=str(path),
        )

    def _skill_to_skill_md(self, skill: Skill) -> str:
        """将 Skill 转换为 SKILL.md 格式的字符串。"""
        return self._skill_to_skill_md_static(skill)

    @staticmethod
    def _skill_to_skill_md_static(skill: Skill) -> str:
        """将 Skill 转换为 SKILL.md 格式的字符串（无实例依赖）。"""
        # 构建 frontmatter 数据（不含 instructions，instructions 放在 body）
        data: dict[str, Any] = {
            "name": skill.name,
            "description": skill.description,
        }
        if skill.tools:
            data["tools"] = skill.tools
        data["meta"] = {
            "version": skill.meta.version,
            "author": skill.meta.author,
            "tags": skill.meta.tags,
            "category": skill.meta.category,
        }
        if skill.meta.extra:
            data["meta"]["extra"] = skill.meta.extra

        frontmatter = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return f"---\n{frontmatter}---\n\n{skill.instructions}\n"

    def _skill_to_file(self, skill: Skill, path: Path) -> None:
        """将 Skill 写入指定文件路径（不更新 index）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self._skill_to_skill_md(skill)
        path.write_text(content, encoding="utf-8")

    def _update_index(self, skill: Skill) -> None:
        """更新 index.json 中的技能索引。"""
        self._ensure_dirs()
        index = self._read_index()
        index[skill.name] = {
            "source": skill.source,
            "version": skill.meta.version,
            "path": skill.path,
        }
        self._write_index(index)

    def _remove_from_index(self, name: str) -> None:
        """从 index.json 中移除技能。"""
        index = self._read_index()
        index.pop(name, None)
        self._write_index(index)

    def _read_index(self) -> dict[str, Any]:
        """读取 index.json。"""
        if not self._index_path.exists():
            return {}
        try:
            with open(self._index_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_index(self, index: dict[str, Any]) -> None:
        """写入 index.json。"""
        self._ensure_dirs()
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
