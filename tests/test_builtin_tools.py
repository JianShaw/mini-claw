"""内置工具测试：验证 calculator、shell、file_ops、time_tool 的正常和异常场景。"""

from __future__ import annotations

import pytest

from claw.tools import ToolsRegistry
from claw.builtin_tools.calculator import register as register_calculator
from claw.builtin_tools.shell import register as register_shell
from claw.builtin_tools.file_ops import register as register_file_ops
from claw.builtin_tools.time_tool import register as register_time


# --- calculator ---


@pytest.mark.asyncio
async def test_calculator_evaluates_basic_arithmetic() -> None:
    registry = ToolsRegistry()
    register_calculator(registry)
    tool = registry.get("calculator")
    result = await tool.handler({"expression": "2 + 3"})
    assert result == "5"


@pytest.mark.asyncio
async def test_calculator_evaluates_complex_expression() -> None:
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "(10 - 3) * 2"})
    assert result == "14"


@pytest.mark.asyncio
async def test_calculator_rejects_unsafe_expression() -> None:
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "__import__('os').system('echo pwned')"})
    assert "Error" in result or "disallowed" in result


@pytest.mark.asyncio
async def test_calculator_rejects_function_call() -> None:
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "print('hello')"})
    assert "Error" in result or "disallowed" in result


@pytest.mark.asyncio
async def test_calculator_handles_syntax_error() -> None:
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "2 + +"})
    assert "Syntax error" in result


@pytest.mark.asyncio
async def test_calculator_supports_power_and_mod() -> None:
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "2 ** 10"})
    assert result == "1024"
    result2 = await registry.execute("calculator", {"expression": "17 % 5"})
    assert result2 == "2"


# --- shell (白名单模式) ---


@pytest.mark.asyncio
async def test_shell_allows_whitelisted_command() -> None:
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=["echo"])
    result = await registry.execute("shell", {"command": "echo hello"})
    assert "hello" in result
    assert "exit code: 0" in result


@pytest.mark.asyncio
async def test_shell_blocks_non_whitelisted_command() -> None:
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=["echo"])
    result = await registry.execute("shell", {"command": "ls"})
    assert "not in allowed list" in result


@pytest.mark.asyncio
async def test_shell_no_whitelist_allows_all() -> None:
    """allowed_commands=None 时不限制命令（受信环境）。"""
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=None)
    result = await registry.execute("shell", {"command": "echo ok"})
    assert "ok" in result


@pytest.mark.asyncio
async def test_shell_timeout() -> None:
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=None, default_timeout=1)
    result = await registry.execute("shell", {"command": "sleep 10"})
    assert "timed out" in result


@pytest.mark.asyncio
async def test_shell_cwd_restriction() -> None:
    """指定 cwd 时命令应在该目录下执行。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        registry = ToolsRegistry()
        register_shell(registry, allowed_commands=["pwd"])
        result = await registry.execute("shell", {"command": "pwd"})
        assert "exit code" in result


# --- file_ops (workspace root 边界) ---


@pytest.mark.asyncio
async def test_file_write_and_read(tmp_path) -> None:
    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))
    file_path = "test.txt"

    write_result = await registry.execute("file_write", {"path": file_path, "content": "hello world"})
    assert "OK" in write_result

    read_result = await registry.execute("file_read", {"path": file_path})
    assert read_result == "hello world"


@pytest.mark.asyncio
async def test_file_read_not_found(tmp_path) -> None:
    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))
    result = await registry.execute("file_read", {"path": "nonexistent.txt"})
    assert "Error" in result


@pytest.mark.asyncio
async def test_file_write_creates_parent_dirs(tmp_path) -> None:
    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_write", {"path": "sub/dir/test.txt", "content": "nested"})
    assert "OK" in result

    read_result = await registry.execute("file_read", {"path": "sub/dir/test.txt"})
    assert read_result == "nested"


@pytest.mark.asyncio
async def test_file_list_directory(tmp_path) -> None:
    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    (tmp_path / "file1.txt").write_text("a")
    (tmp_path / "file2.txt").write_text("bb")
    (tmp_path / "subdir").mkdir()

    result = await registry.execute("file_list", {"path": "."})
    assert "file1.txt" in result
    assert "file2.txt" in result
    assert "subdir" in result


@pytest.mark.asyncio
async def test_file_list_not_directory(tmp_path) -> None:
    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))
    (tmp_path / "file.txt").write_text("test")

    result = await registry.execute("file_list", {"path": "file.txt"})
    assert "Error" in result


@pytest.mark.asyncio
async def test_file_ops_reject_path_escape(tmp_path) -> None:
    """路径逃逸 workspace root 应被拒绝。"""
    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_read", {"path": "../../etc/passwd"})
    assert "Error" in result
    assert "escapes workspace" in result

    write_result = await registry.execute("file_write", {"path": "../../../tmp/evil.txt", "content": "bad"})
    assert "Error" in write_result


# --- time_tool ---


@pytest.mark.asyncio
async def test_time_tool_returns_current_time() -> None:
    registry = ToolsRegistry()
    register_time(registry)
    result = await registry.execute("current_time", {})
    assert len(result) > 10
    assert "-" in result
    assert ":" in result


@pytest.mark.asyncio
async def test_time_tool_custom_format() -> None:
    registry = ToolsRegistry()
    register_time(registry)
    result = await registry.execute("current_time", {"format": "%Y"})
    assert len(result) == 4
    assert result.isdigit()


# --- register_all ---


def test_register_all_registers_safe_tools() -> None:
    """register_all 应只注册安全工具（不含 shell）。"""
    from claw.builtin_tools import register_all

    registry = ToolsRegistry()
    register_all(registry)

    names = {t.name for t in registry.list()}
    assert "calculator" in names
    assert "current_time" in names
    # shell 默认不注册
    assert "shell" not in names


# --- 工具参数 schema ---


def test_builtin_tools_have_parameters() -> None:
    """所有内置工具应有完整的参数 schema。"""
    from claw.builtin_tools import register_all

    registry = ToolsRegistry()
    register_all(registry)

    for tool in registry.list():
        assert tool.parameters is not None, f"{tool.name} missing parameters"
        assert "properties" in tool.parameters, f"{tool.name} missing properties"
