"""Tool API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolSchema(BaseModel):
    """A callable tool exposed to Agents."""

    name: str
    description: str
    parameters: dict[str, Any] | None = None

    @classmethod
    def from_tool(cls, tool) -> ToolSchema:
        return cls(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
        )
