"""测试 claw/expert/marketplace 模块。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claw.expert.marketplace import ExpertMarketplace
from claw.expert.store import SqliteExpertStore
from claw.expert.types import Expert
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
def marketplace(store: SqliteExpertStore) -> ExpertMarketplace:
    return ExpertMarketplace(store)


class TestMarketplace:
    def test_list_available_empty(self, marketplace: ExpertMarketplace) -> None:
        experts = marketplace.list_available()
        assert experts == []

    def test_install_bundled(self, marketplace: ExpertMarketplace) -> None:
        expert = marketplace.install_bundled("general-assistant")
        assert expert.name == "general-assistant"
        experts = marketplace.list_available()
        assert len(experts) >= 1

    def test_get_detail(self, marketplace: ExpertMarketplace) -> None:
        marketplace.install_bundled("code-helper")
        expert = marketplace.get_detail("code-helper")
        assert expert is not None
        assert expert.display_name == "Code Helper"

    def test_list_with_filter(self, marketplace: ExpertMarketplace) -> None:
        marketplace.install_bundled("general-assistant")
        marketplace.install_bundled("code-helper")
        results = marketplace.list_available(q="code")
        assert len(results) == 1

    def test_install_from_file(self, marketplace: ExpertMarketplace, tmp_path: Path) -> None:
        content = "---\nname: custom\n---\nCustom prompt"
        expert_file = tmp_path / "EXPERT.md"
        expert_file.write_text(content, encoding="utf-8")
        expert = marketplace.install_from_file(expert_file)
        assert expert.name == "custom"

    def test_uninstall(self, marketplace: ExpertMarketplace, tmp_path: Path) -> None:
        content = "---\nname: removable\n---\nRemovable prompt"
        expert_file = tmp_path / "EXPERT.md"
        expert_file.write_text(content, encoding="utf-8")
        marketplace.install_from_file(expert_file)
        assert marketplace.get_detail("removable") is not None
        marketplace.uninstall("removable")
        assert marketplace.get_detail("removable") is None
