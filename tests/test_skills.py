"""技能注册表测试：注册、查找、重复注册拒绝、列表。"""

from __future__ import annotations

import pytest

from claw.skills import Skill, SkillsRegistry


def test_skills_registry_registers_and_gets_skill() -> None:
    """注册技能后，能通过名称查找到同一个对象。"""
    registry = SkillsRegistry()
    skill = Skill(name="translate", description="translate text", instructions="...")
    registry.register(skill)
    assert registry.get("translate") is skill


def test_skills_registry_rejects_duplicate_skill_names() -> None:
    """重复注册同名技能应抛出 ValueError。"""
    registry = SkillsRegistry()
    registry.register(Skill(name="translate", description="v1", instructions="..."))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Skill(name="translate", description="v2", instructions="..."))


def test_skills_registry_lists_registered_skills() -> None:
    """list() 应返回所有已注册的技能。"""
    registry = SkillsRegistry()
    registry.register(Skill(name="a", description="a", instructions="..."))
    registry.register(Skill(name="b", description="b", instructions="..."))
    names = {s.name for s in registry.list()}
    assert names == {"a", "b"}
