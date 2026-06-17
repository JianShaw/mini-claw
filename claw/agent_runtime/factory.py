"""Agent 工厂：从 Expert 创建 Agent 实例。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from claw.agent_runtime.store import SqliteAgentStore
from claw.agent_runtime.types import AgentConfig
from claw.expert.store import SqliteExpertStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_sandbox(agent_id: str, sandbox_config: dict) -> None:
    """确保 agent 的 sandbox 目录存在。

    如果 sandbox_config 未指定 sandbox_root，自动分配
    data/sandboxes/{agent_id} 并写回 sandbox_config。
    """
    if not sandbox_config.get("sandbox_root"):
        sandbox_config["sandbox_root"] = f"data/sandboxes/{agent_id}"

    p = Path(sandbox_config["sandbox_root"]).resolve()
    p.mkdir(parents=True, exist_ok=True)


class AgentFactory:
    """从 Expert 模板创建 Agent 运行实例。"""

    def __init__(
        self,
        expert_store: SqliteExpertStore,
        agent_store: SqliteAgentStore,
    ) -> None:
        self._expert_store = expert_store
        self._agent_store = agent_store

    def create_from_expert(
        self,
        expert_name: str,
        agent_name: str | None = None,
    ) -> AgentConfig:
        """从 Expert 创建 Agent 实例，复制所有配置字段。

        每个 Expert 最多对应一个 Agent（幂等）：若已有则直接返回。

        Args:
            expert_name: 源 Expert 名称
            agent_name: 可选的 Agent 显示名称，默认用 Expert 的 display_name

        Returns:
            AgentConfig（已有或新创建，均已持久化）

        Raises:
            ValueError: Expert 不存在
        """
        expert = self._expert_store.get(expert_name)
        if expert is None:
            raise ValueError(f"Expert 不存在: {expert_name}")

        # 幂等：同一 Expert 不重复创建 Agent
        existing = self._agent_store.find_by_source_expert(expert_name)
        if existing is not None:
            return existing

        agent_id = f"ag_{uuid4().hex[:12]}"
        sandbox = dict(expert.default_sandbox)
        # 自动分配并创建 sandbox 目录
        _ensure_sandbox(agent_id, sandbox)

        agent = AgentConfig(
            id=agent_id,
            name=agent_name or expert.display_name,
            source_expert=expert.name,
            system_prompt=expert.system_prompt,        # 复制，非引用
            enabled_skills=list(expert.default_skills),
            enabled_tools=list(expert.default_tools),
            enabled_mcp_servers=list(expert.default_mcp_servers),
            model_config=dict(expert.default_model),
            memory_config=dict(expert.default_memory),
            sandbox_config=sandbox,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self._agent_store.save(agent)
        return agent
