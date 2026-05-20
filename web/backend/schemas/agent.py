"""Agent 相关 Pydantic Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    source_expert: str
    system_prompt: str
    enabled_skills: list[str] = []
    enabled_tools: list[str] = []
    enabled_mcp_servers: list[str] = []
    llm_model: dict[str, Any] = Field(default={}, alias="model_config")
    memory_config: dict[str, Any] = {}
    sandbox_config: dict[str, Any] = {}
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_agent(cls, agent) -> AgentSchema:
        return cls(
            id=agent.id,
            name=agent.name,
            source_expert=agent.source_expert,
            system_prompt=agent.system_prompt,
            enabled_skills=agent.enabled_skills,
            enabled_tools=agent.enabled_tools,
            enabled_mcp_servers=agent.enabled_mcp_servers,
            llm_model=agent.model_config,
            memory_config=agent.memory_config,
            sandbox_config=agent.sandbox_config,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )


class CreateAgentRequest(BaseModel):
    expert_name: str
    agent_name: str | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    enabled_skills: list[str] | None = None
    enabled_tools: list[str] | None = None
    enabled_mcp_servers: list[str] | None = None
    llm_model: dict[str, Any] | None = Field(default=None, alias="model_config")
