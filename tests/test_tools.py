"""工具注册表测试：注册、查找、重复注册拒绝、列表。"""

from __future__ import annotations

import pytest

from claw.tools import Tool, ToolsRegistry


async def _noop_handler(args: dict) -> None:
    """空处理函数，仅用于测试。"""
    pass


def test_tools_registry_registers_and_gets_tool() -> None:
    """注册工具后，能通过名称查找到同一个对象。"""
    registry = ToolsRegistry()
    tool = Tool(name="search", description="search docs", handler=_noop_handler)
    registry.register(tool)
    assert registry.get("search") is tool


def test_tools_registry_rejects_duplicate_tool_names() -> None:
    """重复注册同名工具应抛出 ValueError。"""
    registry = ToolsRegistry()
    tool = Tool(name="search", description="v1", handler=_noop_handler)
    registry.register(tool)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Tool(name="search", description="v2", handler=_noop_handler))


def test_tools_registry_lists_registered_tools() -> None:
    """list() 应返回所有已注册的工具。"""
    registry = ToolsRegistry()
    t1 = Tool(name="a", description="a", handler=_noop_handler)
    t2 = Tool(name="b", description="b", handler=_noop_handler)
    registry.register(t1)
    registry.register(t2)
    names = {t.name for t in registry.list()}
    assert names == {"a", "b"}
