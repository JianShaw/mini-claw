"""工具注册表测试：注册、查找、重复注册拒绝、列表、OpenAI 格式、执行。"""

from __future__ import annotations

import pytest

from claw.tools import Tool, ToolsRegistry


async def _noop_handler(args: dict) -> None:
    """空处理函数，仅用于测试。"""
    pass


async def _echo_handler(args: dict) -> str:
    """回显处理函数，返回参数中的 text 字段。"""
    return args.get("text", "")


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


def test_tool_accepts_parameters() -> None:
    """Tool 可以携带 JSON Schema 参数定义。"""
    params = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    tool = Tool(name="search", description="search", handler=_noop_handler, parameters=params)
    assert tool.parameters == params


def test_tool_parameters_default_none() -> None:
    """Tool 的 parameters 默认为 None。"""
    tool = Tool(name="test", description="test", handler=_noop_handler)
    assert tool.parameters is None


def test_tools_registry_to_openai_tools_empty() -> None:
    """空注册表返回空列表。"""
    registry = ToolsRegistry()
    assert registry.to_openai_tools() == []


def test_tools_registry_to_openai_tools_format() -> None:
    """to_openai_tools() 应生成正确的 OpenAI function calling 格式。"""
    registry = ToolsRegistry()
    params = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "search query"}},
        "required": ["query"],
    }
    registry.register(Tool(name="search", description="search the web", handler=_noop_handler, parameters=params))
    result = registry.to_openai_tools()
    assert len(result) == 1
    assert result[0] == {
        "type": "function",
        "function": {
            "name": "search",
            "description": "search the web",
            "parameters": params,
        },
    }


def test_tools_registry_to_openai_tools_no_parameters_defaults_empty_object() -> None:
    """无 parameters 的工具应生成空 object schema。"""
    registry = ToolsRegistry()
    registry.register(Tool(name="test", description="a tool", handler=_noop_handler))
    result = registry.to_openai_tools()
    assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_tools_registry_execute_calls_handler() -> None:
    """execute() 应查找并调用对应工具的 handler。"""
    registry = ToolsRegistry()
    registry.register(Tool(name="echo", description="echo", handler=_echo_handler))
    result = await registry.execute("echo", {"text": "hello"})
    assert result == "hello"


@pytest.mark.asyncio
async def test_tools_registry_execute_raises_keyerror_for_unknown() -> None:
    """execute() 找不到工具时应抛出 KeyError。"""
    registry = ToolsRegistry()
    with pytest.raises(KeyError, match="tool not found"):
        await registry.execute("nonexistent", {})
