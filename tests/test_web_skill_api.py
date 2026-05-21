"""测试 Skills Web API：列表、详情、安装、卸载、导出。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.expert.store import SqliteExpertStore
from claw.gateway import RuntimeGateway
from claw.session import InMemorySessionStore
from claw.skills.marketplace import MarketplaceOps
from claw.skills.registry import SkillsRegistry
from claw.skills.store import SkillStore
from claw.storage.sqlite import get_connection, init_db
from web.backend.app import create_app


class EchoRunner:
    async def run(self, session, message):
        from claw.types import AgentReply
        return AgentReply(text=f"echo: {message.text}")

    async def run_stream(self, session, message):
        from claw.types import StreamChunk
        yield StreamChunk(type="content", text=f"echo: {message.text}")


class SilentDelivery:
    async def send(self, message, reply):
        pass


# 测试用 SKILL.md 内容
_SAMPLE_SKILL_MD = """\
---
name: test-skill
description: A skill for testing installation
meta:
  version: "1.0.0"
  author: tester
  tags:
    - test
  category: testing
tools:
  - file_read
---

# Test Skill Instructions

This is a test skill for validating the installation pipeline.

1. Read the file
2. Analyze the content
3. Report findings
"""

_SAMPLE_SKILL_MD_2 = """\
---
name: another-skill
description: Another test skill
meta:
  version: "2.0.0"
  tags:
    - demo
---

# Another Skill

Do something else.
"""


@pytest.fixture
def conn(tmp_path: Path) -> Any:
    c = get_connection(tmp_path / "test.sqlite")
    init_db(c)
    return c


@pytest.fixture
def skill_store(tmp_path: Path) -> SkillStore:
    # 使用默认 bundled_dir（claw/skills/bundled/），root 指向 tmp_path
    return SkillStore(root=str(tmp_path / "skills"))


@pytest.fixture
def skill_registry() -> SkillsRegistry:
    return SkillsRegistry()


@pytest.fixture
def marketplace(skill_store: SkillStore, skill_registry: SkillsRegistry) -> MarketplaceOps:
    return MarketplaceOps(skill_store, skill_registry)


@pytest.fixture
def app(conn: Any, skill_store: SkillStore, skill_registry: SkillsRegistry, marketplace: MarketplaceOps):
    expert_store = SqliteExpertStore(conn)
    agent_store = SqliteAgentStore(conn)
    expert_store.init_bundled()
    agent_store.ensure_default()

    # 加载 bundled 技能到 registry
    for skill in skill_store.load_all():
        skill_registry.register(skill)

    resolver = AgentResolver(agent_store)
    gateway = RuntimeGateway(
        session_store=InMemorySessionStore(),
        agent_runner=EchoRunner(),
        delivery=SilentDelivery(),
        agent_resolver=resolver,
    )

    app = create_app(
        gateway=gateway,
        expert_store=expert_store,
        agent_store=agent_store,
    )

    # 覆盖 skills 依赖为测试实例
    from web.backend.deps import get_marketplace_ops, get_skill_registry, get_skill_store
    app.dependency_overrides[get_skill_store] = lambda: skill_store
    app.dependency_overrides[get_skill_registry] = lambda: skill_registry
    app.dependency_overrides[get_marketplace_ops] = lambda: marketplace

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---- Tests ----


class TestSkillListAPI:
    def test_list_skills(self, client):
        """列出所有技能，应包含 bundled 技能。"""
        resp = client.get("/api/v1/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        # bundled 技能应包含 code-review
        names = [s["name"] for s in data]
        assert "code-review" in names

    def test_list_skills_with_search(self, client):
        """按关键词搜索技能。"""
        resp = client.get("/api/v1/skills", params={"q": "review"})
        assert resp.status_code == 200
        data = resp.json()
        names = [s["name"] for s in data]
        assert "code-review" in names

    def test_list_skills_search_no_results(self, client):
        """搜索无匹配结果。"""
        resp = client.get("/api/v1/skills", params={"q": "nonexistent-skill-xyz"})
        assert resp.status_code == 200
        assert resp.json() == []


class TestSkillDetailAPI:
    def test_get_skill(self, client):
        """获取单个技能详情（含 instructions）。"""
        resp = client.get("/api/v1/skills/code-review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "code-review"
        assert "instructions" in data
        assert len(data["instructions"]) > 0

    def test_get_skill_not_found(self, client):
        """技能不存在返回 404。"""
        resp = client.get("/api/v1/skills/nonexistent-skill")
        assert resp.status_code == 404


class TestSkillInstallAPI:
    def test_install_from_file(self, client):
        """从 SKILL.md 文件上传安装技能。"""
        resp = client.post(
            "/api/v1/skills/install/file",
            files={"file": ("SKILL.md", _SAMPLE_SKILL_MD, "text/markdown")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-skill"
        assert data["source"] == "local"

    def test_install_from_invalid_file(self, client):
        """上传无效文件返回 400。"""
        resp = client.post(
            "/api/v1/skills/install/file",
            files={"file": ("SKILL.md", "not valid yaml", "text/markdown")},
        )
        assert resp.status_code == 400

    def test_install_from_zip(self, tmp_path: Path, client):
        """从 ZIP 压缩包批量安装技能。"""
        # 构建包含 SKILL.md 的 ZIP
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("test-zip-skill/SKILL.md", _SAMPLE_SKILL_MD)
        zip_buf.seek(0)

        resp = client.post(
            "/api/v1/skills/install/zip",
            files={"file": ("skills.zip", zip_buf.read(), "application/zip")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        names = [s["name"] for s in data]
        assert "test-skill" in names

    def test_install_from_empty_zip(self, client):
        """上传空 ZIP 返回 400。"""
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("readme.txt", "no skill file here")
        zip_buf.seek(0)

        resp = client.post(
            "/api/v1/skills/install/zip",
            files={"file": ("empty.zip", zip_buf.read(), "application/zip")},
        )
        assert resp.status_code == 400


class TestSkillUninstallAPI:
    def test_uninstall_local_skill(self, client):
        """卸载用户安装的 local 技能。"""
        # 先安装
        client.post(
            "/api/v1/skills/install/file",
            files={"file": ("SKILL.md", _SAMPLE_SKILL_MD, "text/markdown")},
        )
        # 再卸载
        resp = client.delete("/api/v1/skills/test-skill")
        assert resp.status_code == 204

    def test_uninstall_bundled_forbidden(self, client):
        """bundled 技能不可卸载，返回 400。"""
        resp = client.delete("/api/v1/skills/code-review")
        assert resp.status_code == 400
        assert "bundled" in resp.json()["detail"].lower()

    def test_uninstall_not_found(self, client):
        """不存在的技能卸载返回 404。"""
        resp = client.delete("/api/v1/skills/nonexistent-skill")
        assert resp.status_code == 404


class TestSkillExportAPI:
    def test_export_skill(self, client):
        """导出单个技能为 SKILL.md 文件。"""
        resp = client.get("/api/v1/skills/code-review/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
        content = resp.text
        assert "code-review" in content
        assert "---" in content  # YAML frontmatter

    def test_export_skill_not_found(self, client):
        """导出不存在的技能返回 404。"""
        resp = client.get("/api/v1/skills/nonexistent/export")
        assert resp.status_code == 404

    def test_export_skills_batch(self, client):
        """批量导出多个技能为 ZIP。"""
        resp = client.post(
            "/api/v1/skills/export",
            json={"names": ["code-review", "translate"]},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        # 验证返回的内容是有效 ZIP
        zip_buf = io.BytesIO(resp.content)
        with zipfile.ZipFile(zip_buf, "r") as zf:
            names = zf.namelist()
            assert any("code-review" in n for n in names)
            assert any("translate" in n for n in names)
