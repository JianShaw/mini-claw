"""Agent 运行实例存储：SQLite 持久化。"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from claw.agent_runtime.types import AgentConfig

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteAgentStore:
    """SQLite 持久化 Agent 存储器。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, agent: AgentConfig) -> None:
        """保存 Agent（INSERT OR REPLACE）。"""
        now = _now_iso()
        if not agent.created_at:
            agent.created_at = now
        agent.updated_at = now

        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agents (
                    id, name, source_expert, system_prompt,
                    enabled_skills_json, enabled_tools_json, enabled_mcp_servers_json,
                    model_config_json, memory_config_json, sandbox_config_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent.id,
                    agent.name,
                    agent.source_expert,
                    agent.system_prompt,
                    json.dumps(agent.enabled_skills, ensure_ascii=False),
                    json.dumps(agent.enabled_tools, ensure_ascii=False),
                    json.dumps(agent.enabled_mcp_servers, ensure_ascii=False),
                    json.dumps(agent.model_config, ensure_ascii=False),
                    json.dumps(agent.memory_config, ensure_ascii=False),
                    json.dumps(agent.sandbox_config, ensure_ascii=False),
                    agent.created_at,
                    agent.updated_at,
                ),
            )

    def get(self, agent_id: str) -> AgentConfig | None:
        """按 ID 获取 Agent，不存在返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_agent(row)

    def list_all(self) -> list[AgentConfig]:
        """列出所有 Agent。"""
        rows = self._conn.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
        return [self._row_to_agent(r) for r in rows]

    def delete(self, agent_id: str) -> bool:
        """删除 Agent，返回是否成功。"""
        cursor = self._conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def find_by_source_expert(self, expert_name: str) -> AgentConfig | None:
        """按 source_expert 查找 Agent（每个 Expert 最多一个 Agent）。"""
        row = self._conn.execute(
            "SELECT * FROM agents WHERE source_expert = ? LIMIT 1",
            (expert_name,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_agent(row)

    def exists(self, agent_id: str) -> bool:
        """检查 Agent 是否存在。"""
        row = self._conn.execute(
            "SELECT 1 FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        return row is not None

    def ensure_default(self) -> AgentConfig:
        """确保 default-agent 存在，不存在则创建。"""
        agent = self.get("default-agent")
        if agent is not None:
            return agent

        agent = AgentConfig(
            id="default-agent",
            name="Default Agent",
            source_expert="",
            system_prompt="You are Mini Claw, a helpful assistant.",
            enabled_skills=[],
            enabled_tools=["calculator", "get_current_time", "file_search", "load_skill"],
            enabled_mcp_servers=[],
            model_config={"provider": "deepseek", "name": "deepseek-chat"},
            memory_config={"enabled": True},
            sandbox_config={"workspace_required": True, "sandbox_root": "data/sandboxes/default-agent"},
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self.save(agent)
        logger.info("创建默认 Agent: default-agent")
        return agent

    def _row_to_agent(self, row: sqlite3.Row) -> AgentConfig:
        """将 SQLite 行转为 AgentConfig 对象。"""
        return AgentConfig(
            id=row["id"],
            name=row["name"],
            source_expert=row["source_expert"],
            system_prompt=row["system_prompt"],
            enabled_skills=json.loads(row["enabled_skills_json"]),
            enabled_tools=json.loads(row["enabled_tools_json"]),
            enabled_mcp_servers=json.loads(row["enabled_mcp_servers_json"]),
            model_config=json.loads(row["model_config_json"]),
            memory_config=json.loads(row["memory_config_json"]),
            sandbox_config=json.loads(row["sandbox_config_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
