"""技能注册表：预留 Agent 可用的技能/提示词扩展点。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Skill:
    """技能定义：名称、描述、指令文本。"""
    name: str
    description: str
    instructions: str


class SkillsRegistry:
    """技能注册表：注册、查找、列举技能。不允许重复注册同名技能。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        return list(self._skills.values())
