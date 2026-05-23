"""Agent REST API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from claw.agent_runtime.factory import AgentFactory
from claw.agent_runtime.store import SqliteAgentStore
from claw.skills.registry import SkillsRegistry
from claw.tools import ToolsRegistry
from web.backend.deps import get_agent_factory, get_agent_store, get_skill_registry
from web.backend.schemas.agent import AgentSchema, CreateAgentRequest, UpdateAgentRequest

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentSchema, status_code=201)
async def create_agent(
    request: CreateAgentRequest,
    factory: AgentFactory = Depends(get_agent_factory),
) -> AgentSchema:
    try:
        agent = factory.create_from_expert(request.expert_name, request.agent_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AgentSchema.from_agent(agent)


@router.get("", response_model=list[AgentSchema])
async def list_agents(
    store: SqliteAgentStore = Depends(get_agent_store),
) -> list[AgentSchema]:
    agents = store.list_all()
    return [AgentSchema.from_agent(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentSchema)
async def get_agent(
    agent_id: str,
    store: SqliteAgentStore = Depends(get_agent_store),
) -> AgentSchema:
    agent = store.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return AgentSchema.from_agent(agent)


@router.put("/{agent_id}", response_model=AgentSchema)
async def update_agent(
    agent_id: str,
    request: UpdateAgentRequest,
    http_request: Request,
    store: SqliteAgentStore = Depends(get_agent_store),
    skill_registry: SkillsRegistry = Depends(get_skill_registry),
) -> AgentSchema:
    agent = store.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    # 按需更新字段
    if request.name is not None:
        agent.name = request.name
    if request.system_prompt is not None:
        agent.system_prompt = request.system_prompt
    if request.enabled_skills is not None:
        _validate_skills(request.enabled_skills, skill_registry)
        agent.enabled_skills = request.enabled_skills
    if request.enabled_tools is not None:
        tools_registry: ToolsRegistry = http_request.app.state.tools_registry
        _validate_tools(request.enabled_tools, tools_registry)
        agent.enabled_tools = request.enabled_tools
    if request.enabled_mcp_servers is not None:
        agent.enabled_mcp_servers = request.enabled_mcp_servers
    if request.llm_model is not None:
        agent.model_config = {
            **agent.model_config,
            **_normalize_model_config(request.llm_model),
        }
    store.save(agent)
    return AgentSchema.from_agent(agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    store: SqliteAgentStore = Depends(get_agent_store),
) -> None:
    if not store.delete(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")


def _validate_skills(names: list[str], registry: SkillsRegistry) -> None:
    """Reject stale skill names before persisting Agent config."""
    available = {s.name for s in registry.list()}
    missing = sorted(set(names) - available)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown skills: {', '.join(missing)}",
        )


def _validate_tools(names: list[str], registry: ToolsRegistry) -> None:
    """Reject stale tool names before persisting Agent config."""
    available = {t.name for t in registry.list()}
    missing = sorted(set(names) - available)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tools: {', '.join(missing)}",
        )


def _normalize_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Keep only runtime-tunable model fields.

    Provider and model name are fixed by the current claw runner, so Web must not
    allow users to persist a misleading provider/model override.
    """
    allowed = {"temperature"}
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported model config fields: {', '.join(unknown)}",
        )

    normalized: dict[str, Any] = {}
    if "temperature" in config:
        temperature = config["temperature"]
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise HTTPException(status_code=422, detail="model_config.temperature must be a number")
        if temperature < 0 or temperature > 2:
            raise HTTPException(status_code=422, detail="model_config.temperature must be between 0 and 2")
        normalized["temperature"] = temperature
    return normalized
