"""Tools that let the agent create scheduled tasks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from claw.tools import Tool, ToolsRegistry

CreateTaskHandler = Callable[[dict[str, Any]], Awaitable[str]]


def register(registry: ToolsRegistry, handler: CreateTaskHandler) -> None:
    """Register the scheduled task creation tool."""
    if registry.get("create_scheduled_task") is not None:
        return
    registry.register(Tool(
        name="create_scheduled_task",
        description=(
            "Create or update a scheduled task from the user's natural-language "
            "request. Use this when the user asks to remind them, run something "
            "later, repeat something on an interval, or schedule an agent action."
        ),
        handler=handler,
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Stable short task name, lowercase words separated by underscores.",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description of the scheduled task.",
                },
                "trigger": {
                    "type": "object",
                    "description": "When the task should run.",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["cron", "interval"],
                        },
                        "expression": {
                            "type": "string",
                            "description": "Five-field cron expression when type is cron.",
                        },
                        "seconds": {
                            "type": "integer",
                            "description": "Interval in seconds when type is interval.",
                        },
                    },
                    "required": ["type"],
                },
                "prompt": {
                    "type": "string",
                    "description": "The exact instruction the agent should execute when the task fires.",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Whether the task should start enabled. Defaults to true.",
                },
                "replace": {
                    "type": "boolean",
                    "description": "Set true to replace an existing task with the same name.",
                },
            },
            "required": ["name", "trigger", "prompt"],
        },
    ))
