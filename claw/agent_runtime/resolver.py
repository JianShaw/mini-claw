"""Agent 解析器：按 session.agent_id 解析 RuntimeProfile。"""

from __future__ import annotations

from pathlib import Path

from claw.agent_runtime.store import SqliteAgentStore
from claw.agent_runtime.types import AgentConfig, RuntimeProfile

# 默认 sandbox 根目录（相对于项目根）
_DEFAULT_SANDBOX_BASE = "data/sandboxes"


class AgentResolver:
    """按 session.agent_id 解析本轮运行配置。

    解析规则：
    1. 按 agent_id 查找 AgentConfig
    2. 找不到时回退到 default-agent
    3. default-agent 也不存在时自动创建
    """

    def __init__(
        self,
        agent_store: SqliteAgentStore,
        *,
        default_agent_id: str = "default-agent",
    ) -> None:
        self._agent_store = agent_store
        self._default_agent_id = default_agent_id

    def resolve(self, agent_id: str | None) -> RuntimeProfile:
        """解析 agent_id 为 RuntimeProfile。

        Args:
            agent_id: session.agent_id，可为 None（回退到 default）

        Returns:
            本轮有效的 RuntimeProfile
        """
        agent = self._find_agent(agent_id)
        return self._to_profile(agent)

    def _find_agent(self, agent_id: str | None) -> AgentConfig:
        """按优先级查找 Agent：指定 ID → default-agent → 创建 default。"""
        # 1. 尝试按指定 ID 查找
        if agent_id:
            agent = self._agent_store.get(agent_id)
            if agent is not None:
                return agent

        # 2. 回退到 default-agent
        if agent_id != self._default_agent_id:
            agent = self._agent_store.get(self._default_agent_id)
            if agent is not None:
                return agent

        # 3. 确保默认 Agent 存在
        return self._agent_store.ensure_default()

    @staticmethod
    def _resolve_sandbox_root(agent: AgentConfig) -> str:
        """从 sandbox_config 解析 sandbox 绝对路径。

        优先级：sandbox_config.sandbox_root > data/sandboxes/{agent_id}
        相对路径基于当前工作目录解析，自动创建目录。
        """
        sandbox = agent.sandbox_config
        configured = sandbox.get("sandbox_root", "")

        if configured:
            p = Path(configured)
        else:
            # 默认 sandbox：data/sandboxes/{agent_id}
            p = Path(_DEFAULT_SANDBOX_BASE) / agent.id

        # 相对路径基于 CWD 解析
        resolved = p.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return str(resolved)

    def _to_profile(self, agent: AgentConfig) -> RuntimeProfile:
        """将 AgentConfig 转为 RuntimeProfile（深拷贝可变字段 + 解析 sandbox）。"""
        return RuntimeProfile(
            agent_id=agent.id,
            system_prompt=agent.system_prompt,
            model_config=dict(agent.model_config),
            enabled_skills=list(agent.enabled_skills),
            enabled_tools=list(agent.enabled_tools),
            enabled_mcp_servers=list(agent.enabled_mcp_servers),
            memory_config=dict(agent.memory_config),
            sandbox_config=dict(agent.sandbox_config),
            sandbox_root=self._resolve_sandbox_root(agent),
        )
