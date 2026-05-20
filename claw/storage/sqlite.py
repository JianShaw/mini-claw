"""SQLite 连接管理、schema 迁移、事务工具。

所有 Expert / Agent / Session 数据统一存到 data/mini_claw.sqlite。
使用 stdlib sqlite3，不引入 ORM；初始化时设置 WAL 模式、执行 migration。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

_DEFAULT_DB_PATH = "data/mini_claw.sqlite"

# ---- schema 版本号，每次 migration 变更时递增 ----
_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS experts (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    default_skills_json TEXT NOT NULL DEFAULT '[]',
    default_tools_json TEXT NOT NULL DEFAULT '[]',
    default_mcp_servers_json TEXT NOT NULL DEFAULT '[]',
    default_model_json TEXT NOT NULL DEFAULT '{}',
    default_memory_json TEXT NOT NULL DEFAULT '{}',
    default_sandbox_json TEXT NOT NULL DEFAULT '{}',
    meta_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    source_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_expert TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    enabled_skills_json TEXT NOT NULL DEFAULT '[]',
    enabled_tools_json TEXT NOT NULL DEFAULT '[]',
    enabled_mcp_servers_json TEXT NOT NULL DEFAULT '[]',
    model_config_json TEXT NOT NULL DEFAULT '{}',
    memory_config_json TEXT NOT NULL DEFAULT '{}',
    sandbox_config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    channel TEXT NOT NULL,
    account_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT,
    history_offset INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls_json TEXT,
    tool_call_id TEXT,
    tool_name TEXT,
    reasoning_content TEXT,
    ts INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS active_sessions (
    session_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def get_connection(db_path: str | Path = _DEFAULT_DB_PATH) -> sqlite3.Connection:
    """获取 SQLite 连接，启用 WAL 模式和外键约束。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """执行 schema migration（幂等），确保所有表存在。"""
    with conn:
        conn.executescript(_SCHEMA_SQL)
        # 写入版本号（INSERT OR IGNORE 保证幂等）
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Cursor, None, None]:
    """事务上下文管理器：自动 commit / rollback。"""
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
