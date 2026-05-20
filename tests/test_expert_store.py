"""测试 claw/expert/store 模块。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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


def _make_expert(name: str = "test-expert", **overrides) -> Expert:
    defaults = dict(
        name=name,
        display_name="Test Expert",
        description="A test expert",
        system_prompt="You are a test expert.",
        default_tools=["calculator"],
        default_skills=["code-review"],
        meta=ExpertMeta(tags=["test"], category="testing", avatar="🧪"),
    )
    defaults.update(overrides)
    return Expert(**defaults)


class TestParseExpertMd:
    def test_parse_valid(self) -> None:
        content = """---
name: my-expert
display_name: My Expert
description: A great expert
default_tools:
  - calculator
  - file_search
meta:
  version: "1.0.0"
  tags: [code, dev]
  category: dev
  avatar: "🤖"
---

You are an expert assistant.
"""
        expert = SqliteExpertStore.parse_expert_md(content, source="local")
        assert expert.name == "my-expert"
        assert expert.display_name == "My Expert"
        assert expert.system_prompt == "You are an expert assistant."
        assert expert.default_tools == ["calculator", "file_search"]
        assert expert.meta.tags == ["code", "dev"]
        assert expert.meta.version == "1.0.0"
        assert expert.source == "local"

    def test_parse_missing_frontmatter(self) -> None:
        with pytest.raises(ValueError, match="YAML frontmatter"):
            SqliteExpertStore.parse_expert_md("no frontmatter")

    def test_parse_missing_name(self) -> None:
        content = "---\ndescription: no name\n---\nbody"
        with pytest.raises(ValueError, match="name"):
            SqliteExpertStore.parse_expert_md(content)

    def test_parse_invalid_name(self) -> None:
        content = "---\nname: INVALID\n---\nbody"
        with pytest.raises(ValueError, match="名称不合法"):
            SqliteExpertStore.parse_expert_md(content)

    def test_parse_empty_body(self) -> None:
        content = "---\nname: test\n---\n"
        with pytest.raises(ValueError, match="system_prompt"):
            SqliteExpertStore.parse_expert_md(content)

    def test_parse_minimal(self) -> None:
        content = "---\nname: minimal\n---\nHello"
        expert = SqliteExpertStore.parse_expert_md(content)
        assert expert.name == "minimal"
        assert expert.display_name == "minimal"
        assert expert.default_tools == []
        assert expert.meta.tags == []


class TestParseExpertMdFile:
    def test_parse_file(self, tmp_path: Path) -> None:
        content = "---\nname: file-expert\n---\nFile expert prompt"
        expert_file = tmp_path / "EXPERT.md"
        expert_file.write_text(content, encoding="utf-8")
        expert = SqliteExpertStore.parse_expert_md_file(expert_file, source="bundled")
        assert expert.name == "file-expert"
        assert expert.source == "bundled"


class TestExpertToMd:
    def test_roundtrip(self) -> None:
        original = _make_expert()
        md = SqliteExpertStore.expert_to_md(original)
        parsed = SqliteExpertStore.parse_expert_md(md)
        assert parsed.name == original.name
        assert parsed.display_name == original.display_name
        assert parsed.system_prompt == original.system_prompt
        assert parsed.default_tools == original.default_tools
        assert parsed.default_skills == original.default_skills
        assert parsed.meta.tags == original.meta.tags
        assert parsed.meta.category == original.meta.category


class TestSqliteExpertStore:
    def test_save_and_get(self, store: SqliteExpertStore) -> None:
        expert = _make_expert()
        store.save(expert)
        loaded = store.get("test-expert")
        assert loaded is not None
        assert loaded.name == "test-expert"
        assert loaded.display_name == "Test Expert"
        assert loaded.system_prompt == "You are a test expert."
        assert loaded.default_tools == ["calculator"]

    def test_get_nonexistent(self, store: SqliteExpertStore) -> None:
        assert store.get("nonexistent") is None

    def test_list_all(self, store: SqliteExpertStore) -> None:
        store.save(_make_expert("expert-a"))
        store.save(_make_expert("expert-b"))
        all_experts = store.list_all()
        names = {e.name for e in all_experts}
        assert "expert-a" in names
        assert "expert-b" in names

    def test_delete(self, store: SqliteExpertStore) -> None:
        store.save(_make_expert())
        assert store.delete("test-expert") is True
        assert store.get("test-expert") is None

    def test_delete_nonexistent(self, store: SqliteExpertStore) -> None:
        assert store.delete("nonexistent") is False

    def test_exists(self, store: SqliteExpertStore) -> None:
        assert store.exists("test-expert") is False
        store.save(_make_expert())
        assert store.exists("test-expert") is True

    def test_save_upsert(self, store: SqliteExpertStore) -> None:
        store.save(_make_expert(system_prompt="v1"))
        store.save(_make_expert(system_prompt="v2"))
        loaded = store.get("test-expert")
        assert loaded is not None
        assert loaded.system_prompt == "v2"

    def test_init_bundled(self, store: SqliteExpertStore) -> None:
        imported = store.init_bundled()
        assert len(imported) > 0
        # 幂等：再次调用不应重复导入
        imported2 = store.init_bundled()
        assert len(imported2) == 0

    def test_init_bundled_experts_queryable(self, store: SqliteExpertStore) -> None:
        store.init_bundled()
        expert = store.get("general-assistant")
        assert expert is not None
        assert expert.source == "bundled"
        assert expert.system_prompt  # 非空

    def test_json_fields_preserved(self, store: SqliteExpertStore) -> None:
        expert = _make_expert(
            default_model={"provider": "deepseek", "name": "deepseek-chat", "temperature": 0.5},
            default_memory={"enabled": True},
        )
        store.save(expert)
        loaded = store.get("test-expert")
        assert loaded is not None
        assert loaded.default_model["provider"] == "deepseek"
        assert loaded.default_model["temperature"] == 0.5
        assert loaded.default_memory["enabled"] is True
