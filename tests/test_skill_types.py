"""测试技能数据模型：Skill、SkillMeta。"""

from __future__ import annotations

import pytest

from claw.skills.types import Skill, SkillMeta, SkillLoadError


class TestSkillMeta:
    def test_defaults(self):
        meta = SkillMeta()
        assert meta.version == "1.0.0"
        assert meta.author == ""
        assert meta.tags == []
        assert meta.category == ""
        assert meta.extra == {}

    def test_custom_values(self):
        meta = SkillMeta(
            version="2.0.0",
            author="test",
            tags=["a", "b"],
            category="test-cat",
            extra={"key": "val"},
        )
        assert meta.version == "2.0.0"
        assert meta.author == "test"
        assert meta.tags == ["a", "b"]
        assert meta.category == "test-cat"
        assert meta.extra == {"key": "val"}


class TestSkill:
    def test_basic_construction(self):
        skill = Skill(name="test", description="A test skill", instructions="Do stuff")
        assert skill.name == "test"
        assert skill.description == "A test skill"
        assert skill.instructions == "Do stuff"
        assert skill.tools == []
        assert skill.source == "local"
        assert skill.path is None

    def test_full_construction(self):
        meta = SkillMeta(version="1.0", tags=["x"])
        skill = Skill(
            name="my-skill",
            description="desc",
            instructions="instr",
            tools=["tool1", "tool2"],
            meta=meta,
            source="bundled",
            path="bundled/my-skill/SKILL.md",
        )
        assert skill.tools == ["tool1", "tool2"]
        assert skill.meta.version == "1.0"
        assert skill.source == "bundled"
        assert skill.path == "bundled/my-skill/SKILL.md"

    def test_slash_name(self):
        skill = Skill(name="code-review", description="d", instructions="i")
        assert skill.slash_name == "/code-review"

    def test_slash_name_with_underscores_invalid(self):
        """含下划线的名称在构造时就会被拒绝。"""
        with pytest.raises(ValueError, match="Invalid skill name"):
            Skill(name="code_review", description="d", instructions="i")

    def test_slash_name_with_spaces_invalid(self):
        """含空格的名称在构造时就会被拒绝。"""
        with pytest.raises(ValueError, match="Invalid skill name"):
            Skill(name="code review", description="d", instructions="i")

    def test_slash_name_already_hyphenated(self):
        skill = Skill(name="my-cool-skill", description="d", instructions="i")
        assert skill.slash_name == "/my-cool-skill"

    def test_is_valid_name(self):
        assert Skill.is_valid_name("code-review") is True
        assert Skill.is_valid_name("a") is True
        assert Skill.is_valid_name("abc123") is True
        assert Skill.is_valid_name("my-cool-skill-v2") is True

    def test_is_valid_name_invalid(self):
        assert Skill.is_valid_name("") is False
        assert Skill.is_valid_name("Code-Review") is False  # 大写
        assert Skill.is_valid_name("code_review") is False  # 下划线
        assert Skill.is_valid_name("-start") is False  # 以连字符开头
        assert Skill.is_valid_name("a" * 65) is False  # 超长
        assert Skill.is_valid_name("has space") is False

    def test_meta_defaults_in_skill(self):
        skill = Skill(name="test", description="d", instructions="i")
        assert skill.meta.version == "1.0.0"
        assert skill.meta.tags == []


class TestSkillLoadError:
    def test_is_exception(self):
        err = SkillLoadError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"
