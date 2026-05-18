"""测试本地技能市场操作：MarketplaceOps（SKILL.md 格式）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import yaml

from claw.skills.marketplace import MarketplaceOps
from claw.skills.registry import SkillsRegistry
from claw.skills.store import SkillStore
from claw.skills.types import Skill, SkillMeta

_SKILL_FILE = "SKILL.md"


@pytest.fixture
def store(tmp_path: Path) -> SkillStore:
    return SkillStore(root=tmp_path / "skills", bundled_dir=tmp_path / "skills" / "bundled")


@pytest.fixture
def registry() -> SkillsRegistry:
    return SkillsRegistry()


@pytest.fixture
def market(store: SkillStore, registry: SkillsRegistry) -> MarketplaceOps:
    return MarketplaceOps(store, registry)


def _write_skill_md(path: Path, frontmatter: dict, body: str = "Test instructions") -> None:
    """写入 SKILL.md 格式文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    path.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")


class TestMarketplaceInstallFromFile:
    def test_install_from_skill_md(self, market: MarketplaceOps, tmp_path: Path):
        skill_dir = tmp_path / "my-skill"
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "my-skill",
            "description": "A custom skill",
        }, "Do custom things")
        skill = market.install_from_file(skill_dir / _SKILL_FILE)
        assert skill.name == "my-skill"
        assert skill.source == "local"

    def test_install_registers_in_registry(self, market: MarketplaceOps, tmp_path: Path, registry: SkillsRegistry):
        skill_dir = tmp_path / "new"
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "new-skill",
            "description": "New skill",
        }, "New instructions")
        market.install_from_file(skill_dir / _SKILL_FILE)
        assert registry.get("new-skill") is not None

    def test_install_nonexistent_file(self, market: MarketplaceOps, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            market.install_from_file(tmp_path / "nope" / _SKILL_FILE)

    def test_install_duplicate_updates(self, market: MarketplaceOps, tmp_path: Path, registry: SkillsRegistry):
        skill_dir = tmp_path / "dup"
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "dup-skill",
            "description": "First version",
        }, "First")
        market.install_from_file(skill_dir / _SKILL_FILE)

        # 更新文件
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "dup-skill",
            "description": "Second version",
        }, "Second")
        market.install_from_file(skill_dir / _SKILL_FILE)
        assert registry.get("dup-skill").description == "Second version"


class TestMarketplaceInstallFromZip:
    def test_install_from_zip(self, market: MarketplaceOps, tmp_path: Path, registry: SkillsRegistry):
        # 创建一个包含 SKILL.md 的 ZIP
        zip_path = tmp_path / "skills.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            # 技能目录中的 SKILL.md
            fm_a = yaml.dump({"name": "skill-a", "description": "Skill A"}, allow_unicode=True)
            zf.writestr("skill-a/SKILL.md", f"---\n{fm_a}---\n\nA instructions\n")
            fm_b = yaml.dump({"name": "skill-b", "description": "Skill B"}, allow_unicode=True)
            zf.writestr("skill-b/SKILL.md", f"---\n{fm_b}---\n\nB instructions\n")

        installed = market.install_from_zip(zip_path)
        assert len(installed) == 2
        names = {s.name for s in installed}
        assert names == {"skill-a", "skill-b"}
        assert registry.get("skill-a") is not None

    def test_install_from_empty_zip(self, market: MarketplaceOps, tmp_path: Path):
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("readme.txt", "no skills here")

        with pytest.raises(ValueError, match="没有 SKILL.md"):
            market.install_from_zip(zip_path)

    def test_install_from_nonexistent_zip(self, market: MarketplaceOps, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            market.install_from_zip(tmp_path / "nope.zip")


class TestMarketplaceInstallFromDirectory:
    def test_install_from_dir(self, market: MarketplaceOps, tmp_path: Path, registry: SkillsRegistry):
        parent_dir = tmp_path / "to_install"
        # 创建两个技能子目录
        skill_a_dir = parent_dir / "dir-a"
        _write_skill_md(skill_a_dir / _SKILL_FILE, {
            "name": "dir-a",
            "description": "Dir A",
        }, "A")
        skill_b_dir = parent_dir / "dir-b"
        _write_skill_md(skill_b_dir / _SKILL_FILE, {
            "name": "dir-b",
            "description": "Dir B",
        }, "B")

        installed = market.install_from_directory(parent_dir)
        assert len(installed) == 2
        assert registry.get("dir-a") is not None

    def test_install_single_skill_dir(self, market: MarketplaceOps, tmp_path: Path, registry: SkillsRegistry):
        """直接安装包含 SKILL.md 的单个技能目录。"""
        skill_dir = tmp_path / "single-skill"
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "single",
            "description": "Single skill",
        }, "Single instructions")

        installed = market.install_from_directory(skill_dir)
        assert len(installed) == 1
        assert installed[0].name == "single"

    def test_install_from_nonexistent_dir(self, market: MarketplaceOps, tmp_path: Path):
        with pytest.raises(NotADirectoryError):
            market.install_from_directory(tmp_path / "nope")


class TestMarketplaceRemove:
    def test_remove_installed(self, market: MarketplaceOps, tmp_path: Path, registry: SkillsRegistry):
        skill_dir = tmp_path / "rem"
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "rem-skill",
            "description": "To remove",
        }, "Remove me")
        market.install_from_file(skill_dir / _SKILL_FILE)
        assert registry.get("rem-skill") is not None

        result = market.remove("rem-skill")
        assert result is True
        assert registry.get("rem-skill") is None

    def test_remove_nonexistent(self, market: MarketplaceOps):
        result = market.remove("no-such-skill")
        assert result is False


class TestMarketplaceExport:
    def test_export_single_skill(self, market: MarketplaceOps, tmp_path: Path):
        # 先安装一个技能
        skill_dir = tmp_path / "exp"
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "exp-skill",
            "description": "Export me",
        }, "Export instructions")
        market.install_from_file(skill_dir / _SKILL_FILE)

        dest = tmp_path / "export"
        out = market.export_skill("exp-skill", dest)
        assert out.exists()
        assert out.name == _SKILL_FILE

    def test_export_multiple_skills(self, market: MarketplaceOps, tmp_path: Path):
        for name in ["a", "b"]:
            skill_dir = tmp_path / f"export-{name}"
            _write_skill_md(skill_dir / _SKILL_FILE, {
                "name": f"export-{name}",
                "description": f"Skill {name}",
            }, f"{name}")
            market.install_from_file(skill_dir / _SKILL_FILE)

        dest = tmp_path / "export"
        out = market.export_skills(["export-a", "export-b"], dest)
        assert out.exists()
        assert out.suffix == ".zip"

        # 验证 ZIP 内容
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert f"export-a/{_SKILL_FILE}" in names
            assert f"export-b/{_SKILL_FILE}" in names

    def test_export_nonexistent_skill(self, market: MarketplaceOps, tmp_path: Path):
        with pytest.raises(KeyError, match="不存在"):
            market.export_skill("nope", tmp_path / "out")


class TestMarketplaceInfo:
    def test_info_existing(self, market: MarketplaceOps, tmp_path: Path):
        skill_dir = tmp_path / "info"
        _write_skill_md(skill_dir / _SKILL_FILE, {
            "name": "info-skill",
            "description": "Info skill",
            "meta": {"version": "2.0.0", "tags": ["info"]},
        }, "Info instructions")
        market.install_from_file(skill_dir / _SKILL_FILE)

        info = market.info("info-skill")
        assert info["name"] == "info-skill"
        assert info["meta"]["version"] == "2.0.0"
        assert "info" in info["meta"]["tags"]

    def test_info_nonexistent(self, market: MarketplaceOps):
        assert market.info("nope") == {}


class TestMarketplaceListInstalled:
    def test_list_installed(self, market: MarketplaceOps, tmp_path: Path):
        for name in ["a", "b"]:
            skill_dir = tmp_path / f"list-{name}"
            _write_skill_md(skill_dir / _SKILL_FILE, {
                "name": f"list-{name}",
                "description": f"Skill {name}",
            }, f"{name}")
            market.install_from_file(skill_dir / _SKILL_FILE)

        installed = market.list_installed()
        assert len(installed) == 2
        names = {s["name"] for s in installed}
        assert names == {"list-a", "list-b"}

    def test_list_empty(self, market: MarketplaceOps):
        assert market.list_installed() == []
