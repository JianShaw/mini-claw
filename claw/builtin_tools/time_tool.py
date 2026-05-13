"""时间工具：返回当前日期和时间。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from claw.tools import Tool, ToolsRegistry


async def _get_current_time(args: dict[str, Any]) -> str:
    fmt = args.get("format", "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    return now.strftime(fmt)


def register(registry: ToolsRegistry) -> None:
    registry.register(Tool(
        name="current_time",
        description="Get the current date and time.",
        handler=_get_current_time,
        parameters={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "description": "strftime format string (default '%Y-%m-%d %H:%M:%S')",
                },
            },
            "required": [],
        },
    ))
