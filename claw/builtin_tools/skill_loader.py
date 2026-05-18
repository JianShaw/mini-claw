"""技能加载工具：LLM 通过 tool call 按需加载技能的完整指令。

两层技能加载机制的核心：
- Layer 1（系统提示）：轻量级索引（name + description），始终可见
- Layer 2（tool_result）：完整指令，仅在 LLM 调用 load_skill 时按需返回

这样避免了把所有技能的完整指令都塞进系统提示词，大幅节省 token。
"""

from __future__ import annotations

from typing import Any

from claw.tools import Tool, ToolsRegistry

_skills_registry: Any = None


async def _load_skill(args: dict[str, Any]) -> str:
    """加载技能的完整指令，通过 tool_result 注入对话。"""
    global _skills_registry
    if _skills_registry is None:
        return "Error: skills system not configured."

    name = args.get("name", "").strip()
    if not name:
        return "Error: 'name' parameter is required."

    skill = _skills_registry.get(name)
    if skill is None:
        available = ", ".join(s.name for s in _skills_registry.list())
        return f"Error: skill '{name}' not found. Available skills: {available}"

    # 格式化完整指令
    parts = [f'<skill name="{skill.name}">', skill.instructions]
    if skill.tools:
        parts.append(f"Available tools: {', '.join(skill.tools)}")
    parts.append("</skill>")
    return "\n".join(parts)


def register(registry: ToolsRegistry, skills_registry: Any) -> None:
    """注册 load_skill 工具。"""
    global _skills_registry
    _skills_registry = skills_registry
    registry.register(Tool(
        name="load_skill",
        description=(
            "Load a skill's full instructions by name. "
            "Call this when you decide to use a specific skill's workflow. "
            "The skill instructions will be returned as the tool result."
        ),
        handler=_load_skill,
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The skill name to load (e.g. 'code-review', 'translate', 'summarize')",
                },
            },
            "required": ["name"],
        },
    ))
