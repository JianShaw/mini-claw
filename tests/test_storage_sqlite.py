"""测试 claw/storage/sqlite 模块。"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from claw.storage.sqlite import get_connection, init_db, transaction


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite"


@pytest.fixture
def conn(db_path: Path) -> sqlite3.Connection:
    c = get_connection(db_path)
    init_db(c)
    yield c
    c.close()


class TestGetConnection:
    def test_creates_db_file(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        assert db_path.exists()
        conn.close()

    def test_wal_mode(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"
        conn.close()

    def test_foreign_keys_on(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1
        conn.close()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "dir" / "test.sqlite"
        conn = get_connection(db_path)
        assert db_path.exists()
        conn.close()


class TestInitDb:
    def test_creates_all_tables(self, conn: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "experts" in tables
        assert "agents" in tables
        assert "sessions" in tables
        assert "session_messages" in tables
        assert "active_sessions" in tables
        assert "schema_version" in tables

    def test_idempotent(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        init_db(conn)
        # 再次调用不应报错
        init_db(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "experts" in tables
        conn.close()


class TestTransaction:
    def test_commit_on_success(self, conn: sqlite3.Connection) -> None:
        with transaction(conn) as cur:
            cur.execute(
                "INSERT INTO experts (name, display_name, description, system_prompt, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                ("test-expert", "Test", "Desc", "Prompt", "local"),
            )
        row = conn.execute("SELECT name FROM experts WHERE name = ?", ("test-expert",)).fetchone()
        assert row is not None

    def test_rollback_on_error(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="test error"):
            with transaction(conn) as cur:
                cur.execute(
                    "INSERT INTO experts (name, display_name, description, system_prompt, source, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                    ("rollback-test", "Test", "Desc", "Prompt", "local"),
                )
                raise ValueError("test error")
        # 数据不应存在
        row = conn.execute("SELECT name FROM experts WHERE name = ?", ("rollback-test",)).fetchone()
        assert row is None
