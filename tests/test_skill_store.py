"""测试技能文件存储：SkillStore 的保存/加载/删除/列表功能（SKILL.md 格式）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from claw.skills.store import SkillStore
from claw.skills.types import Skill, SkillMeta, SkillLoadError


_SKILL_FILE = "SKILL.md"


@pytest.fixture
def store(tmp_path: Path) -> SkillStore:
    """创建临时目录的 SkillStore（bundled_dir 也指向临时目录，避免写包目录）。"""
    return SkillStore(root=tmp_path / "skills", bundled_dir=tmp_path / "skills" / "bundled")


@pytest.fixture
def sample_skill() -> Skill:
    """创建测试用技能。"""
    return Skill(
        name="test-skill",
        description="A test skill",
        instructions="Do test things",
        tools=["tool1"],
        meta=SkillMeta(version="1.0.0", tags=["test"]),
        source="local",
    )


def _write_skill_md(path: Path, frontmatter: dict, body: str = "Test instructions") -> None:
    """写入 SKILL.md 格式文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    path.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")


class TestSkillStoreSave:
    def test_save_creates_skill_md(self, store: SkillStore, sample_skill: Skill):
        path = store.save(sample_skill)
        assert path.exists()
        assert path.name == _SKILL_FILE

    def test_save_creates_skill_directory(self, store: SkillStore, sample_skill: Skill):
        path = store.save(sample_skill)
        assert path.parent.name == "test-skill"

    def test_save_local_goes_to_local_dir(self, store: SkillStore, sample_skill: Skill):
        path = store.save(sample_skill)
        assert "local" in str(path)

    def test_save_bundled_goes_to_bundled_dir(self, store: SkillStore):
        skill = Skill(
            name="bundled-skill",
            description="bundled",
            instructions="bundled instructions",
            source="bundled",
        )
        path = store.save(skill)
        assert "bundled" in str(path)

    def test_save_updates_index(self, store: SkillStore, sample_skill: Skill):
        store.save(sample_skill)
        index = store._read_index()
        assert "test-skill" in index
        assert index["test-skill"]["source"] == "local"

    def test_save_overwrites_existing(self, store: SkillStore, sample_skill: Skill):
        store.save(sample_skill)
        sample_skill.description = "Updated description"
        store.save(sample_skill)
        loaded = store.load("test-skill")
        assert loaded is not None
        assert loaded.description == "Updated description"

    def test_save_skill_md_format(self, store: SkillStore, sample_skill: Skill):
        """保存的文件应为 SKILL.md 格式（frontmatter + body）。"""
        path = store.save(sample_skill)
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---")
        # body 应包含 instructions
        assert "Do test things" in content
        # frontmatter 应包含 name 和 description
        assert "test-skill" in content


class TestSkillStoreLoad:
    def test_load_existing(self, store: SkillStore, sample_skill: Skill):
        store.save(sample_skill)
        loaded = store.load("test-skill")
        assert loaded is not None
        assert loaded.name == "test-skill"
        assert loaded.description == "A test skill"
        assert loaded.instructions == "Do test things"
        assert loaded.tools == ["tool1"]

    def test_load_nonexistent(self, store: SkillStore):
        result = store.load("no-such-skill")
        assert result is None

    def test_load_prefers_local_over_bundled(self, store: SkillStore):
        bundled = Skill(
            name="my-skill", description="bundled version",
            instructions="bundled", source="bundled",
        )
        local = Skill(
            name="my-skill", description="local version",
            instructions="local", source="local",
        )
        store.save(bundled)
        store.save(local)
        loaded = store.load("my-skill")
        assert loaded.description == "local version"

    def test_load_missing_frontmatter(self, store: SkillStore):
        store._ensure_dirs()
        skill_dir = store._local_dir / "bad"
        skill_dir.mkdir(parents=True, exist_ok=True)
        bad_file = skill_dir / _SKILL_FILE
        bad_file.write_text("Just some text without frontmatter", encoding="utf-8")
        with pytest.raises(SkillLoadError, match="frontmatter"):
            store._load_file(bad_file)

    def test_load_missing_name(self, store: SkillStore):
        store._ensure_dirs()
        skill_dir = store._local_dir / "bad"
        skill_dir.mkdir(parents=True, exist_ok=True)
        _write_skill_md(
            skill_dir / _SKILL_FILE,
            {"description": "test"},
            "Some instructions",
        )
        with pytest.raises(SkillLoadError, match="缺少 name"):
            store._load_file(skill_dir / _SKILL_FILE)

    def test_load_missing_description(self, store: SkillStore):
        store._ensure_dirs()
        skill_dir = store._local_dir / "nodesc"
        skill_dir.mkdir(parents=True, exist_ok=True)
        _write_skill_md(
            skill_dir / _SKILL_FILE,
            {"name": "test"},
            "Some instructions",
        )
        with pytest.raises(SkillLoadError, match="缺少 description"):
            store._load_file(skill_dir / _SKILL_FILE)

    def test_load_missing_instructions_body(self, store: SkillStore):
        store._ensure_dirs()
        skill_dir = store._local_dir / "noins"
        skill_dir.mkdir(parents=True, exist_ok=True)
        fm = yaml.dump({"name": "test", "description": "test"}, allow_unicode=True)
        # 只有 frontmatter，没有 body
        bad_file = skill_dir / _SKILL_FILE
        bad_file.write_text(f"---\n{fm}---\n", encoding="utf-8")
        with pytest.raises(SkillLoadError, match="缺少 instructions"):
            store._load_file(bad_file)

    def test_load_invalid_name(self, store: SkillStore):
        store._ensure_dirs()
        skill_dir = store._local_dir / "badname"
        skill_dir.mkdir(parents=True, exist_ok=True)
        _write_skill_md(
            skill_dir / _SKILL_FILE,
            {"name": "INVALID", "description": "test"},
            "Some instructions",
        )
        with pytest.raises(SkillLoadError, match="名称不合法"):
            store._load_file(skill_dir / _SKILL_FILE)

    def test_load_with_tools_and_meta(self, store: SkillStore):
        store._ensure_dirs()
        skill_dir = store._local_dir / "full"
        skill_dir.mkdir(parents=True, exist_ok=True)
        _write_skill_md(
            skill_dir / _SKILL_FILE,
            {
                "name": "full-skill",
                "description": "Full skill",
                "tools": ["tool1", "tool2"],
                "meta": {"version": "2.0.0", "tags": ["a", "b"]},
            },
            "Full instructions here",
        )
        skill = store._load_file(skill_dir / _SKILL_FILE)
        assert skill.tools == ["tool1", "tool2"]
        assert skill.meta.version == "2.0.0"
        assert skill.meta.tags == ["a", "b"]


class TestSkillStoreDelete:
    def test_delete_existing(self, store: SkillStore, sample_skill: Skill):
        store.save(sample_skill)
        assert store.delete("test-skill") is True
        assert store.load("test-skill") is None

    def test_delete_nonexistent(self, store: SkillStore):
        assert store.delete("no-such-skill") is False

    def test_delete_removes_from_index(self, store: SkillStore, sample_skill: Skill):
        store.save(sample_skill)
        store.delete("test-skill")
        index = store._read_index()
        assert "test-skill" not in index

    def test_delete_removes_directory(self, store: SkillStore, sample_skill: Skill):
        store.save(sample_skill)
        skill_dir = store._local_dir / "test-skill"
        assert skill_dir.is_dir()
        store.delete("test-skill")
        assert not skill_dir.exists()

    def test_delete_rejects_invalid_name(self, store: SkillStore):
        """路径注入攻击：非法名称应被拒绝。"""
        assert store.delete("../../../etc") is False
        assert store.delete("") is False
        assert store.delete("INVALID") is False

    def test_delete_rejects_path_traversal(self, store: SkillStore, tmp_path: Path):
        """即使名称通过 is_valid_name，含 .. 的名称也应被拒绝。"""
        assert store.delete("test-skill") is False  # 不存在

    def test_delete_only_removes_local(self, store: SkillStore):
        """delete 不应删除 bundled 技能。"""
        bundled = Skill(
            name="bundled-skill", description="bundled",
            instructions="bundled", source="bundled",
        )
        store.save(bundled)
        # bundled 保存在 bundled_dir，delete 只删 local
        assert store.delete("bundled-skill") is False


class TestSkillStoreLoadAll:
    def test_load_all_from_empty(self, store: SkillStore):
        skills = store.load_all()
        assert skills == []

    def test_load_all_merges_sources(self, store: SkillStore):
        bundled = Skill(
            name="skill-a", description="bundled a",
            instructions="a", source="bundled",
        )
        local = Skill(
            name="skill-b", description="local b",
            instructions="b", source="local",
        )
        store.save(bundled)
        store.save(local)
        skills = store.load_all()
        names = {s.name for s in skills}
        assert names == {"skill-a", "skill-b"}

    def test_load_all_local_overrides_bundled(self, store: SkillStore):
        bundled = Skill(
            name="overlap", description="bundled",
            instructions="bundled", source="bundled",
        )
        local = Skill(
            name="overlap", description="local",
            instructions="local", source="local",
        )
        store.save(bundled)
        store.save(local)
        skills = store.load_all()
        overlap_skills = [s for s in skills if s.name == "overlap"]
        assert len(overlap_skills) == 1
        assert overlap_skills[0].source == "local"

    def test_load_all_skips_invalid_files(self, store: SkillStore):
        store._ensure_dirs()
        # 写一个有效技能
        valid = Skill(name="valid", description="v", instructions="v")
        store.save(valid)
        # 写一个无效的 SKILL.md
        bad_dir = store._local_dir / "invalid"
        bad_dir.mkdir(parents=True, exist_ok=True)
        bad_file = bad_dir / _SKILL_FILE
        bad_file.write_text("not a valid skill file", encoding="utf-8")
        skills = store.load_all()
        names = {s.name for s in skills}
        assert "valid" in names


class TestSkillStoreListFiles:
    def test_list_files_empty(self, store: SkillStore):
        assert store.list_files() == []

    def test_list_files(self, store: SkillStore, sample_skill: Skill):
        store.save(sample_skill)
        files = store.list_files()
        assert len(files) == 1
        assert files[0].name == _SKILL_FILE
