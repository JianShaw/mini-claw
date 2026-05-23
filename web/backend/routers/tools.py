"""Tool discovery REST API."""

from __future__ import annotations

from fastapi import APIRouter, Request

from claw.tools import ToolsRegistry
from web.backend.schemas.tool import ToolSchema

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=list[ToolSchema])
async def list_tools(request: Request) -> list[ToolSchema]:
    """List built-in tools available for Agent configuration."""
    registry: ToolsRegistry = request.app.state.tools_registry
    tools = sorted(registry.list(), key=lambda t: t.name)
    return [ToolSchema.from_tool(t) for t in tools]
