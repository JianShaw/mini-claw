"""测试 V1 Phase 3c: tools/skills/MCP 按 Agent 配置过滤。"""

from __future__ import annotations

import pytest

from claw.tools import Tool, ToolsRegistry
from claw.skills.registry import SkillsRegistry
from claw.skills.types import Skill, SkillMeta


# ---- Helpers ----

def _make_tool(name: str) -> Tool:
    async def handler(args):
        return f"{name} result"
    return Tool(name=name, description=f"{name} tool", handler=handler)


def _make_skill(name: str, desc: str = "") -> Skill:
    return Skill(
        name=name,
        description=desc or f"{name} skill",
        instructions=f"Instructions for {name}",
        meta=SkillMeta(),
    )


class TestToolsRegistryFiltering:
    def test_no_filter_returns_all(self) -> None:
        reg = ToolsRegistry()
        reg.register(_make_tool("calculator"))
        reg.register(_make_tool("file_search"))
        result = reg.to_openai_tools()
        assert len(result) == 2

    def test_filter_by_enabled_tools(self) -> None:
        reg = ToolsRegistry()
        reg.register(_make_tool("calculator"))
        reg.register(_make_tool("file_search"))
        reg.register(_make_tool("run_command"))
        result = reg.to_openai_tools(enabled_tools=["calculator", "file_search"])
        names = [t["function"]["name"] for t in result]
        assert names == ["calculator", "file_search"]

    def test_empty_enabled_tools_returns_none(self) -> None:
        reg = ToolsRegistry()
        reg.register(_make_tool("calculator"))
        result = reg.to_openai_tools(enabled_tools=[])
        assert len(result) == 0

    def test_mcp_tools_filtered_by_server(self) -> None:
        reg = ToolsRegistry()
        reg.register(_make_tool("calculator"))
        reg.register(_make_tool("github__list_prs"))
        reg.register(_make_tool("github__merge_pr"))
        reg.register(_make_tool("slack__send_msg"))

        # 只允许 github server 的 MCP 工具 + calculator 内置工具
        result = reg.to_openai_tools(
            enabled_tools=["calculator"],
            enabled_mcp_servers=["github"],
        )
        names = [t["function"]["name"] for t in result]
        assert "calculator" in names
        assert "github__list_prs" in names
        assert "github__merge_pr" in names
        assert "slack__send_msg" not in names

    def test_mcp_only_filter(self) -> None:
        reg = ToolsRegistry()
        reg.register(_make_tool("calculator"))
        reg.register(_make_tool("github__list_prs"))

        # 只过滤 MCP servers，不过滤内置工具
        result = reg.to_openai_tools(enabled_mcp_servers=["github"])
        names = [t["function"]["name"] for t in result]
        assert "calculator" in names
        assert "github__list_prs" in names

    def test_empty_mcp_servers_blocks_all_mcp(self) -> None:
        reg = ToolsRegistry()
        reg.register(_make_tool("calculator"))
        reg.register(_make_tool("github__list_prs"))

        result = reg.to_openai_tools(
            enabled_tools=["calculator"],
            enabled_mcp_servers=[],
        )
        names = [t["function"]["name"] for t in result]
        assert names == ["calculator"]


class TestSkillsRegistryFiltering:
    def test_no_filter_returns_all(self) -> None:
        reg = SkillsRegistry()
        reg.register(_make_skill("code-review"))
        reg.register(_make_skill("translate"))
        listing = reg.build_skills_listing()
        assert "code-review" in listing
        assert "translate" in listing

    def test_filter_by_enabled_skills(self) -> None:
        reg = SkillsRegistry()
        reg.register(_make_skill("code-review"))
        reg.register(_make_skill("translate"))
        listing = reg.build_skills_listing(enabled_skills=["code-review"])
        assert "code-review" in listing
        assert "translate" not in listing

    def test_empty_enabled_skills_returns_empty(self) -> None:
        reg = SkillsRegistry()
        reg.register(_make_skill("code-review"))
        listing = reg.build_skills_listing(enabled_skills=[])
        assert listing == ""

    def test_none_enabled_skills_returns_all(self) -> None:
        reg = SkillsRegistry()
        reg.register(_make_skill("code-review"))
        listing = reg.build_skills_listing(enabled_skills=None)
        assert "code-review" in listing
