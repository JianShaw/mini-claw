"""测试 claw/expert/service 模块。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claw.expert.service import ExpertService
from claw.expert.store import SqliteExpertStore
from claw.expert.types import Expert, ExpertMeta
from claw.storage.sqlite import get_connection, init_db


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = get_connection(tmp_path / "test.sqlite")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def store(conn: sqlite3.Connection) -> SqliteExpertStore:
    return SqliteExpertStore(conn)


@pytest.fixture
def service(store: SqliteExpertStore) -> ExpertService:
    return ExpertService(store)


def _make_expert(name: str = "test-expert", **overrides) -> Expert:
    defaults = dict(
        name=name,
        display_name="Test Expert",
        description="A test expert",
        system_prompt="You are a test expert.",
        meta=ExpertMeta(tags=["test"]),
    )
    defaults.update(overrides)
    return Expert(**defaults)


class TestExpertServiceGet:
    def test_get_existing(self, service: ExpertService, store: SqliteExpertStore) -> None:
        store.save(_make_expert())
        expert = service.get("test-expert")
        assert expert is not None
        assert expert.name == "test-expert"

    def test_get_nonexistent(self, service: ExpertService) -> None:
        assert service.get("nonexistent") is None


class TestExpertServiceList:
    def test_list_all(self, service: ExpertService, store: SqliteExpertStore) -> None:
        store.save(_make_expert("expert-a", description="Alpha expert"))
        store.save(_make_expert("expert-b", description="Beta expert"))
        experts = service.list_experts()
        assert len(experts) == 2

    def test_filter_by_query(self, service: ExpertService, store: SqliteExpertStore) -> None:
        store.save(_make_expert("expert-a", description="Code helper"))
        store.save(_make_expert("expert-b", description="Paper reader"))
        results = service.list_experts(q="code")
        assert len(results) == 1
        assert results[0].name == "expert-a"

    def test_filter_by_tag(self, service: ExpertService, store: SqliteExpertStore) -> None:
        store.save(_make_expert("expert-a", meta=ExpertMeta(tags=["code", "dev"])))
        store.save(_make_expert("expert-b", meta=ExpertMeta(tags=["research"])))
        results = service.list_experts(tag="code")
        assert len(results) == 1
        assert results[0].name == "expert-a"

    def test_filter_combined(self, service: ExpertService, store: SqliteExpertStore) -> None:
        store.save(_make_expert("expert-a", description="Code helper", meta=ExpertMeta(tags=["code"])))
        store.save(_make_expert("expert-b", description="Paper reader", meta=ExpertMeta(tags=["code"])))
        results = service.list_experts(q="paper", tag="code")
        assert len(results) == 1
        assert results[0].name == "expert-b"


class TestExpertServiceInstallBundled:
    def test_install(self, service: ExpertService, store: SqliteExpertStore) -> None:
        expert = service.install_bundled("general-assistant")
        assert expert.name == "general-assistant"
        assert store.get("general-assistant") is not None

    def test_install_idempotent(self, service: ExpertService) -> None:
        e1 = service.install_bundled("general-assistant")
        e2 = service.install_bundled("general-assistant")
        assert e1.name == e2.name

    def test_install_nonexistent_bundled(self, service: ExpertService) -> None:
        with pytest.raises(ValueError, match="Bundled 专家不存在"):
            service.install_bundled("nonexistent-expert")


class TestExpertServiceInstallFromFile:
    def test_install_from_file(self, service: ExpertService, tmp_path: Path) -> None:
        content = "---\nname: custom-expert\n---\nCustom prompt"
        expert_file = tmp_path / "EXPERT.md"
        expert_file.write_text(content, encoding="utf-8")
        expert = service.install_from_file(expert_file)
        assert expert.name == "custom-expert"
        assert expert.source == "local"

    def test_install_overwrites_local(self, service: ExpertService, store: SqliteExpertStore, tmp_path: Path) -> None:
        store.save(_make_expert("custom-expert", system_prompt="v1", source="local"))
        content = "---\nname: custom-expert\n---\nv2 prompt"
        expert_file = tmp_path / "EXPERT.md"
        expert_file.write_text(content, encoding="utf-8")
        expert = service.install_from_file(expert_file)
        assert expert.system_prompt == "v2 prompt"

    def test_install_cannot_overwrite_bundled(self, service: ExpertService, store: SqliteExpertStore, tmp_path: Path) -> None:
        store.init_bundled()
        content = "---\nname: general-assistant\n---\nOverwrite prompt"
        expert_file = tmp_path / "EXPERT.md"
        expert_file.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="不允许覆盖 bundled 专家"):
            service.install_from_file(expert_file)


class TestExpertServiceUninstall:
    def test_uninstall_local(self, service: ExpertService, store: SqliteExpertStore) -> None:
        store.save(_make_expert(source="local"))
        service.uninstall("test-expert")
        assert store.get("test-expert") is None

    def test_uninstall_bundled_forbidden(self, service: ExpertService, store: SqliteExpertStore) -> None:
        store.init_bundled()
        with pytest.raises(ValueError, match="不允许卸载 bundled 专家"):
            service.uninstall("general-assistant")

    def test_uninstall_nonexistent(self, service: ExpertService) -> None:
        with pytest.raises(ValueError, match="专家不存在"):
            service.uninstall("nonexistent")
