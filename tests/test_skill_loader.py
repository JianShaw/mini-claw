"""测试技能加载器：SkillLoader 的 SKILL.md 文件解析和优先级加载。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from claw.skills.loader import SkillLoader
from claw.skills.store import SkillStore
from claw.skills.types import Skill, SkillLoadError

_SKILL_FILE = "SKILL.md"


@pytest.fixture
def loader() -> SkillLoader:
    return SkillLoader()


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """创建包含测试技能文件的临时目录。"""
    return tmp_path / "skills"


def _write_skill_md(skill_dir: Path, name: str, frontmatter: dict, body: str = "Test instructions") -> Path:
    """在 skill_dir/{name}/SKILL.md 写入技能文件。"""
    skill_subdir = skill_dir / name
    skill_subdir.mkdir(parents=True, exist_ok=True)
    fm = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    file_path = skill_subdir / _SKILL_FILE
    file_path.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")
    return file_path


class TestSkillLoaderLoadFile:
    def test_load_valid_file(self, loader: SkillLoader, skill_dir: Path):
        path = _write_skill_md(skill_dir, "test", {
            "name": "test-skill",
            "description": "A test skill",
        }, "Do test things")
        skill = loader.load_file(path)
        assert skill.name == "test-skill"
        assert skill.description == "A test skill"
        assert skill.instructions == "Do test things"

    def test_load_full_file(self, loader: SkillLoader, skill_dir: Path):
        path = _write_skill_md(skill_dir, "full", {
            "name": "full-skill",
            "description": "Full skill",
            "tools": ["tool1", "tool2"],
            "meta": {
                "version": "2.0.0",
                "author": "tester",
                "tags": ["a", "b"],
                "category": "test",
            },
        }, "Full instructions here")
        skill = loader.load_file(path)
        assert skill.tools == ["tool1", "tool2"]
        assert skill.meta.version == "2.0.0"
        assert skill.meta.tags == ["a", "b"]
        assert skill.instructions == "Full instructions here"

    def test_load_nonexistent_file(self, loader: SkillLoader, skill_dir: Path):
        with pytest.raises(SkillLoadError, match="文件不存在"):
            loader.load_file(skill_dir / "nope" / _SKILL_FILE)

    def test_load_wrong_filename(self, loader: SkillLoader, skill_dir: Path):
        file = skill_dir / "test.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("{}", encoding="utf-8")
        with pytest.raises(SkillLoadError, match="SKILL.md"):
            loader.load_file(file)

    def test_load_missing_name(self, loader: SkillLoader, skill_dir: Path):
        path = _write_skill_md(skill_dir, "bad", {
            "description": "test",
        }, "Some instructions")
        with pytest.raises(SkillLoadError, match="缺少 name"):
            loader.load_file(path)

    def test_load_missing_instructions(self, loader: SkillLoader, skill_dir: Path):
        skill_subdir = skill_dir / "noins"
        skill_subdir.mkdir(parents=True, exist_ok=True)
        fm = yaml.dump({"name": "test-skill", "description": "test"}, allow_unicode=True)
        file_path = skill_subdir / _SKILL_FILE
        file_path.write_text(f"---\n{fm}---\n", encoding="utf-8")
        with pytest.raises(SkillLoadError, match="缺少 instructions"):
            loader.load_file(file_path)

    def test_load_empty_tools_is_valid(self, loader: SkillLoader, skill_dir: Path):
        path = _write_skill_md(skill_dir, "notools", {
            "name": "notools",
            "description": "No tools skill",
            "tools": [],
        }, "Just instructions")
        skill = loader.load_file(path)
        assert skill.tools == []


class TestSkillLoaderLoadDir:
    def test_load_dir(self, loader: SkillLoader, skill_dir: Path):
        _write_skill_md(skill_dir, "skill-a", {
            "name": "skill-a",
            "description": "A",
        }, "A")
        _write_skill_md(skill_dir, "skill-b", {
            "name": "skill-b",
            "description": "B",
        }, "B")
        skills = loader.load_dir(skill_dir)
        names = {s.name for s in skills}
        assert names == {"skill-a", "skill-b"}

    def test_load_empty_dir(self, loader: SkillLoader, skill_dir: Path):
        skill_dir.mkdir(parents=True, exist_ok=True)
        skills = loader.load_dir(skill_dir)
        assert skills == []

    def test_load_nonexistent_dir(self, loader: SkillLoader, skill_dir: Path):
        skills = loader.load_dir(skill_dir / "nonexistent")
        assert skills == []

    def test_load_dir_skips_invalid(self, loader: SkillLoader, skill_dir: Path):
        _write_skill_md(skill_dir, "valid", {
            "name": "valid",
            "description": "Valid",
        }, "Valid")
        # 无效的 SKILL.md
        bad_dir = skill_dir / "invalid"
        bad_dir.mkdir(parents=True, exist_ok=True)
        bad_file = bad_dir / _SKILL_FILE
        bad_file.write_text("not valid", encoding="utf-8")
        skills = loader.load_dir(skill_dir)
        assert len(skills) == 1
        assert skills[0].name == "valid"

    def test_load_dir_ignores_files(self, loader: SkillLoader, skill_dir: Path):
        """load_dir 应忽略直接在目录下的文件，只扫描子目录。"""
        skill_dir.mkdir(parents=True, exist_ok=True)
        # 直接放一个文件（不是子目录）
        (skill_dir / "orphan.txt").write_text("orphan", encoding="utf-8")
        _write_skill_md(skill_dir, "nested", {
            "name": "nested",
            "description": "Nested skill",
        }, "Nested")
        skills = loader.load_dir(skill_dir)
        assert len(skills) == 1
        assert skills[0].name == "nested"


class TestSkillLoaderPrecedence:
    def test_local_overrides_bundled(self, tmp_path: Path):
        store = SkillStore(root=tmp_path, bundled_dir=tmp_path / "bundled")
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

        loader = SkillLoader()
        skills = loader.load_with_precedence(store)
        overlap = [s for s in skills if s.name == "overlap"]
        assert len(overlap) == 1
        assert overlap[0].source == "local"
