"""Agent 运行实例数据类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentConfig:
    """从 Expert 创建的运行时 Agent 实例配置。

    与 Expert 的区别：Agent 是可变的、用户可编辑的运行配置。
    Expert 是不可变模板，Agent 从 Expert 创建后独立存在。
    """

    id: str                           # 唯一 ID (ag_xxx) 或 "default-agent"
    name: str                         # 用户可自定义的显示名称
    source_expert: str                # 来源 Expert 名称
    system_prompt: str                # 从 Expert 复制，用户可编辑
    enabled_skills: list[str] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)
    enabled_mcp_servers: list[str] = field(default_factory=list)
    model_config: dict[str, Any] = field(default_factory=dict)
    memory_config: dict[str, Any] = field(default_factory=dict)
    sandbox_config: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""              # ISO 8601
    updated_at: str = ""              # ISO 8601


@dataclass(slots=True)
class RuntimeProfile:
    """每轮运行时的有效配置，从 session.agent_id 解析得出。

    与 AgentConfig 的区别：
    - AgentConfig 是持久化存储的配置
    - RuntimeProfile 是运行时解析后的配置，包含回退逻辑
    - 如果 Agent 被删除，RuntimeProfile 会回退到 default-agent
    """

    agent_id: str
    system_prompt: str
    model_config: dict[str, Any] = field(default_factory=dict)
    enabled_skills: list[str] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)
    enabled_mcp_servers: list[str] = field(default_factory=list)
    memory_config: dict[str, Any] = field(default_factory=dict)
    sandbox_config: dict[str, Any] = field(default_factory=dict)
    sandbox_root: str = ""                # 解析后的绝对路径，工具执行时使用
