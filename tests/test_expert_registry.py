"""测试 claw/expert/registry 模块。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claw.expert.registry import ExpertRegistry
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
def registry(store: SqliteExpertStore) -> ExpertRegistry:
    return ExpertRegistry(store)


def _seed_experts(store: SqliteExpertStore) -> None:
    store.save(Expert(
        name="code-helper", display_name="Code Helper",
        description="代码审查与调试专家",
        system_prompt="You help with code.",
        default_tools=["calculator"],
        meta=ExpertMeta(tags=["code", "dev"], category="development"),
    ))
    store.save(Expert(
        name="paper-reader", display_name="Paper Reader",
        description="论文阅读与总结专家",
        system_prompt="You read papers.",
        meta=ExpertMeta(tags=["research", "academic"], category="research"),
    ))
    store.save(Expert(
        name="general-assistant", display_name="General Assistant",
        description="通用对话助手",
        system_prompt="You are general.",
        meta=ExpertMeta(tags=["general"], category="general"),
    ))


class TestExpertRegistry:
    def test_get(self, registry: ExpertRegistry, store: SqliteExpertStore) -> None:
        _seed_experts(store)
        expert = registry.get("code-helper")
        assert expert is not None
        assert expert.display_name == "Code Helper"

    def test_get_nonexistent(self, registry: ExpertRegistry) -> None:
        assert registry.get("nonexistent") is None

    def test_list_all(self, registry: ExpertRegistry, store: SqliteExpertStore) -> None:
        _seed_experts(store)
        experts = registry.list_all()
        assert len(experts) == 3

    def test_list_by_category(self, registry: ExpertRegistry, store: SqliteExpertStore) -> None:
        _seed_experts(store)
        dev_experts = registry.list_by_category("development")
        assert len(dev_experts) == 1
        assert dev_experts[0].name == "code-helper"

    def test_search(self, registry: ExpertRegistry, store: SqliteExpertStore) -> None:
        _seed_experts(store)
        results = registry.search("论文")
        assert len(results) == 1
        assert results[0].name == "paper-reader"

    def test_search_by_name(self, registry: ExpertRegistry, store: SqliteExpertStore) -> None:
        _seed_experts(store)
        results = registry.search("code")
        assert len(results) == 1
        assert results[0].name == "code-helper"

    def test_list_by_tag(self, registry: ExpertRegistry, store: SqliteExpertStore) -> None:
        _seed_experts(store)
        results = registry.list_by_tag("research")
        assert len(results) == 1
        assert results[0].name == "paper-reader"

    def test_list_categories(self, registry: ExpertRegistry, store: SqliteExpertStore) -> None:
        _seed_experts(store)
        categories = registry.list_categories()
        assert "development" in categories
        assert "research" in categories
        assert "general" in categories
