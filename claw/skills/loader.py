"""技能加载器：解析 SKILL.md 文件，支持加载优先级。

SKILL.md 格式对齐 OpenClaw：YAML frontmatter + Markdown body。
每个技能是一个独立目录，包含 SKILL.md 文件。
"""

from __future__ import annotations

from pathlib import Path

from claw.skills.types import Skill, SkillLoadError
from claw.skills.store import SkillStore

_SKILL_FILE = "SKILL.md"


class SkillLoader:
    """技能加载器：从文件系统加载 SKILL.md 技能文件。

    加载优先级（高→低）：
    1. local/    — 用户手动安装的技能（最高优先级，可覆盖内置）
    2. bundled/  — 随 mini-claw 发布的内置技能

    同名技能按优先级覆盖：local 版本会替换 bundled 版本。
    """

    def load_file(self, path: Path) -> Skill:
        """解析单个 SKILL.md 文件为 Skill 对象。"""
        if not path.exists():
            raise SkillLoadError(f"文件不存在: {path}")
        if path.name != _SKILL_FILE:
            raise SkillLoadError(f"技能文件名必须为 {_SKILL_FILE}: {path.name}")

        # 复用 SkillStore 的解析逻辑
        store = SkillStore(root=path.parent.parent)
        return store._load_file(path)

    def load_dir(self, dir_path: Path) -> list[Skill]:
        """批量加载目录下所有子目录中的 SKILL.md 技能文件。

        dir_path 应该是 bundled/ 或 local/ 这样的目录，
        其下的每个子目录代表一个技能。
        """
        if not dir_path.exists():
            return []

        skills: list[Skill] = []
        for skill_dir in sorted(dir_path.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / _SKILL_FILE
            if not skill_file.exists():
                continue
            try:
                skill = self.load_file(skill_file)
                skills.append(skill)
            except SkillLoadError:
                # 跳过无效文件，不中断批量加载
                pass

        return skills

    def load_with_precedence(self, store: SkillStore) -> list[Skill]:
        """按优先级加载技能：local/ 覆盖 bundled/ 同名技能。"""
        return store.load_all()
