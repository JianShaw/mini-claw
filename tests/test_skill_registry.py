"""测试技能注册表。"""

from __future__ import annotations

import pytest

from claw.skills.registry import SkillsRegistry
from claw.skills.types import Skill, SkillMeta


@pytest.fixture
def registry() -> SkillsRegistry:
    return SkillsRegistry()


@pytest.fixture
def sample_skill() -> Skill:
    return Skill(
        name="test-skill",
        description="A test skill for testing",
        instructions="Do test things",
        tools=["tool1"],
        meta=SkillMeta(tags=["test", "unit"]),
    )


class TestSkillsRegistryBasic:
    def test_register_and_get(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        assert registry.get("test-skill") is sample_skill

    def test_reject_duplicate(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(sample_skill)

    def test_get_nonexistent(self, registry: SkillsRegistry):
        assert registry.get("nope") is None

    def test_list_empty(self, registry: SkillsRegistry):
        assert registry.list() == []

    def test_list_all(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        skills = registry.list()
        assert len(skills) == 1
        assert skills[0] is sample_skill


class TestSkillsRegistryUnregister:
    def test_unregister_existing(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        removed = registry.unregister("test-skill")
        assert removed is sample_skill
        assert registry.get("test-skill") is None

    def test_unregister_nonexistent(self, registry: SkillsRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.unregister("nope")

    def test_unregister_active_deactivates(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        registry.activate("test-skill")
        assert registry.active_skill is not None
        registry.unregister("test-skill")
        assert registry.active_skill is None


class TestSkillsRegistryUpdate:
    def test_update_existing(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        updated = Skill(
            name="test-skill",
            description="Updated description",
            instructions="Updated instructions",
        )
        registry.update(updated)
        assert registry.get("test-skill").description == "Updated description"

    def test_update_nonexistent(self, registry: SkillsRegistry):
        skill = Skill(name="nope", description="d", instructions="i")
        with pytest.raises(KeyError, match="not found"):
            registry.update(skill)

    def test_update_active_refreshes_reference(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        registry.activate("test-skill")
        updated = Skill(
            name="test-skill",
            description="New desc",
            instructions="New instr",
        )
        registry.update(updated)
        assert registry.active_skill.description == "New desc"


class TestSkillsRegistrySearch:
    def test_search_by_name(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        results = registry.search("test-skill")
        assert len(results) == 1

    def test_search_by_description(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        results = registry.search("testing")
        assert len(results) == 1

    def test_search_by_tag(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        results = registry.search("unit")
        assert len(results) == 1

    def test_search_case_insensitive(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        results = registry.search("TEST")
        assert len(results) == 1

    def test_search_no_match(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        results = registry.search("xyz123")
        assert results == []


class TestSkillsRegistrySlashName:
    def test_get_by_slash_name(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        found = registry.get_by_slash_name("/test-skill")
        assert found is sample_skill

    def test_get_by_slash_name_with_hyphens(self, registry: SkillsRegistry):
        skill = Skill(name="code-review", description="d", instructions="i")
        registry.register(skill)
        found = registry.get_by_slash_name("/code-review")
        assert found is skill

    def test_get_by_slash_name_not_found(self, registry: SkillsRegistry):
        assert registry.get_by_slash_name("/nope") is None


class TestSkillsRegistryActivate:
    def test_activate(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        result = registry.activate("test-skill")
        assert result is sample_skill
        assert registry.active_skill is sample_skill

    def test_activate_nonexistent(self, registry: SkillsRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.activate("nope")

    def test_deactivate(self, registry: SkillsRegistry, sample_skill: Skill):
        registry.register(sample_skill)
        registry.activate("test-skill")
        registry.deactivate()
        assert registry.active_skill is None

    def test_only_one_active_at_a_time(self, registry: SkillsRegistry):
        skill1 = Skill(name="a", description="a", instructions="a")
        skill2 = Skill(name="b", description="b", instructions="b")
        registry.register(skill1)
        registry.register(skill2)
        registry.activate("a")
        assert registry.active_skill.name == "a"
        registry.activate("b")
        assert registry.active_skill.name == "b"


class TestSkillsRegistryListing:
    """测试轻量级技能索引（Layer 1）。"""

    def test_build_skills_listing_empty(self, registry: SkillsRegistry):
        assert registry.build_skills_listing() == ""

    def test_build_skills_listing_lists_skills(self, registry: SkillsRegistry):
        registry.register(Skill(
            name="translate",
            description="Translate text between languages",
            instructions="Translate instructions",
        ))
        registry.register(Skill(
            name="code-review",
            description="Review code for quality",
            instructions="Review instructions",
            tools=["file_read"],
        ))
        listing = registry.build_skills_listing()
        assert "translate" in listing
        assert "code-review" in listing
        assert "Translate text" in listing
        assert "file_read" in listing
        assert "load_skill" in listing

    def test_build_skills_listing_no_tools(self, registry: SkillsRegistry):
        registry.register(Skill(
            name="notools",
            description="No tools skill",
            instructions="Instructions",
        ))
        listing = registry.build_skills_listing()
        assert "tools:" not in listing

    def test_build_skills_listing_marks_active(self, registry: SkillsRegistry):
        registry.register(Skill(
            name="active-skill",
            description="Active skill",
            instructions="Active",
        ))
        registry.activate("active-skill")
        listing = registry.build_skills_listing()
        assert "active-skill** *" in listing
        assert 'You MUST call `load_skill`' in listing


class TestSkillsRegistryGetInstructions:
    """测试完整技能指令获取（Layer 2）。"""

    def test_get_skill_instructions(self, registry: SkillsRegistry):
        registry.register(Skill(
            name="pdf",
            description="Process PDF files",
            instructions="Step 1: Read PDF\nStep 2: Extract text",
            tools=["file_read"],
        ))
        result = registry.get_skill_instructions("pdf")
        assert result is not None
        assert '<skill name="pdf">' in result
        assert "Step 1: Read PDF" in result
        assert "file_read" in result
        assert "</skill>" in result

    def test_get_skill_instructions_nonexistent(self, registry: SkillsRegistry):
        assert registry.get_skill_instructions("nope") is None

    def test_get_skill_instructions_no_tools(self, registry: SkillsRegistry):
        registry.register(Skill(
            name="notools",
            description="No tools",
            instructions="Just instructions",
        ))
        result = registry.get_skill_instructions("notools")
        assert result is not None
        assert "Available tools" not in result
