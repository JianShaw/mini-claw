"""Skills 子系统：技能注册、存储、加载和 Marketplace。

技能格式对齐 OpenClaw SKILL.md（YAML frontmatter + Markdown body），
技能选择由 LLM 驱动（[ACTIVATE: skill-name] 标记）。
"""

from __future__ import annotations

from claw.skills.types import Skill, SkillLoadError, SkillMeta
from claw.skills.registry import SkillsRegistry

__all__ = [
    "Skill",
    "SkillMeta",
    "SkillLoadError",
    "SkillsRegistry",
]
