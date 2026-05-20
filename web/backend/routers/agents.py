"""Agent REST API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from claw.agent_runtime.factory import AgentFactory
from claw.agent_runtime.store import SqliteAgentStore
from web.backend.deps import get_agent_factory, get_agent_store
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
    store: SqliteAgentStore = Depends(get_agent_store),
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
        agent.enabled_skills = request.enabled_skills
    if request.enabled_tools is not None:
        agent.enabled_tools = request.enabled_tools
    if request.enabled_mcp_servers is not None:
        agent.enabled_mcp_servers = request.enabled_mcp_servers
    if request.llm_model is not None:
        agent.model_config = request.llm_model
    store.save(agent)
    return AgentSchema.from_agent(agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    store: SqliteAgentStore = Depends(get_agent_store),
) -> None:
    if not store.delete(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
