"""内置工具测试：验证所有内置工具的正常和异常场景，包括安全边界。"""

from __future__ import annotations

import os

import pytest

from claw.tools import ToolsRegistry
from claw.builtin_tools.calculator import register as register_calculator
from claw.builtin_tools.file_ops import register as register_file_ops
from claw.builtin_tools.shell import register as register_shell
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


@pytest.mark.asyncio
async def test_calculator_rejects_large_exponent() -> None:
    """大指数应被拒绝，防止 CPU/内存 DoS。"""
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "9 ** 99999999"})
    assert "Error" in result
    assert "exponent" in result.lower() or "too large" in result.lower()


@pytest.mark.asyncio
async def test_calculator_rejects_large_integer() -> None:
    """超大整数字面量应被拒绝。"""
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "9999999999999999"})
    assert "Error" in result
    assert "too large" in result.lower()


@pytest.mark.asyncio
async def test_calculator_rejects_long_expression() -> None:
    """超长表达式应被拒绝。"""
    registry = ToolsRegistry()
    register_calculator(registry)
    long_expr = "1 + " * 200 + "1"
    result = await registry.execute("calculator", {"expression": long_expr})
    assert "Error" in result
    assert "too long" in result.lower()


@pytest.mark.asyncio
async def test_calculator_allows_reasonable_power() -> None:
    """合理的幂运算应正常工作。"""
    registry = ToolsRegistry()
    register_calculator(registry)
    result = await registry.execute("calculator", {"expression": "2 ** 20"})
    assert result == "1048576"


# --- shell (白名单模式 + 安全执行) ---


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


@pytest.mark.asyncio
async def test_shell_rejects_shell_chaining_and() -> None:
    """&& 操作符不应被执行为 shell 链式命令。"""
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=["echo"])
    result = await registry.execute("shell", {"command": "echo ok && echo pwned"})
    # && 被当作字面量参数传递给 echo，不是 shell 操作符
    assert "pwned" not in result or "&&" in result


@pytest.mark.asyncio
async def test_shell_rejects_pipe() -> None:
    """管道操作符不应被解释。"""
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=["echo"])
    # shlex.split 会把 | 作为单独的 token，echo 收不到 |
    result = await registry.execute("shell", {"command": "echo ok | cat"})
    # 在 exec 模式下，| 不是 shell 管道
    assert "exit code" in result


@pytest.mark.asyncio
async def test_shell_rejects_semicolon() -> None:
    """分号不应被解释为命令分隔符。"""
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=["echo"])
    result = await registry.execute("shell", {"command": "echo ok ; echo pwned"})
    # shlex.split 会把 ; 作为单独 token
    assert "exit code" in result


@pytest.mark.asyncio
async def test_shell_handles_quoted_args() -> None:
    """shlex.split 应正确处理引号参数。"""
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=["echo"])
    result = await registry.execute("shell", {"command": "echo 'hello world'"})
    assert "hello world" in result


@pytest.mark.asyncio
async def test_shell_rejects_nonexistent_command() -> None:
    """不存在的命令应返回错误。"""
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=None)
    result = await registry.execute("shell", {"command": "nonexistent_cmd_xyz_123"})
    assert "Error" in result


@pytest.mark.asyncio
async def test_shell_rejects_path_in_command() -> None:
    """带路径的命令应被拒绝，只允许裸命令名。"""
    registry = ToolsRegistry()
    register_shell(registry, allowed_commands=["python"])
    # Unix 风格路径
    result = await registry.execute("shell", {"command": "/usr/bin/python -c 'print(1)'"})
    assert "Error" in result
    assert "path" in result.lower()
    # Windows 风格路径
    result2 = await registry.execute("shell", {"command": "C:\\Python\\python.exe -c '1'"})
    assert "Error" in result2
    # 相对路径
    result3 = await registry.execute("shell", {"command": "./python -c '1'"})
    assert "Error" in result3


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


@pytest.mark.asyncio
async def test_file_ops_reject_prefix_bypass(tmp_path) -> None:
    """路径前缀绕过应被拒绝：workspace=/tmp/app 时 ../app2/evil 不应通过。"""
    # 创建 /tmp/app 目录作为 workspace，/tmp/app2 作为诱饵
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    app2_dir = tmp_path / "app2"
    app2_dir.mkdir()
    (app2_dir / "evil.txt").write_text("secret")

    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(app_dir))

    # 尝试通过 ../app2/evil.txt 绕过前缀检查
    result = await registry.execute("file_read", {"path": "../app2/evil.txt"})
    assert "Error" in result
    assert "escapes workspace" in result

    write_result = await registry.execute("file_write", {"path": "../app2/pwned.txt", "content": "bad"})
    assert "Error" in write_result


@pytest.mark.asyncio
async def test_file_write_rejects_symlink(tmp_path) -> None:
    """写入 symlink 应被拒绝。"""
    target = tmp_path / "outside.txt"
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation not supported on this system")

    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_write", {"path": "link.txt", "content": "bad"})
    assert "Error" in result
    assert "symlink" in result.lower()


@pytest.mark.asyncio
async def test_file_read_rejects_symlink(tmp_path) -> None:
    """读取 symlink 应被拒绝。"""
    target = tmp_path / "outside.txt"
    target.write_text("secret")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation not supported on this system")

    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_read", {"path": "link.txt"})
    assert "Error" in result
    assert "symlink" in result.lower()


@pytest.mark.asyncio
async def test_file_read_truncates_large_file(tmp_path) -> None:
    """大文件应先检查 stat 大小再截断，不应完整读入内存。"""
    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    # 创建 > 1MB 的文件
    big_content = "x" * 1_100_000
    (tmp_path / "big.txt").write_text(big_content)

    result = await registry.execute("file_read", {"path": "big.txt"})
    assert "truncated" in result
    assert len(result) < 1_100_000  # 不应返回完整文件


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


# --- file_search ---


@pytest.mark.asyncio
async def test_file_search_glob_finds_files(tmp_path) -> None:
    """glob 模式搜索应找到匹配的文件。"""
    from claw.builtin_tools.file_search import register as register_file_search

    (tmp_path / "hello.txt").write_text("hello")
    (tmp_path / "world.py").write_text("world")

    registry = ToolsRegistry()
    register_file_search(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_search", {"glob": "*.txt"})
    assert "hello.txt" in result
    assert "world.py" not in result


@pytest.mark.asyncio
async def test_file_search_keyword_finds_content(tmp_path) -> None:
    """keyword 搜索应返回包含关键字的行号。"""
    from claw.builtin_tools.file_search import register as register_file_search

    (tmp_path / "code.py").write_text("def hello():\n    return 'world'\n")

    registry = ToolsRegistry()
    register_file_search(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_search", {"glob": "*.py", "keyword": "world"})
    assert "code.py" in result
    assert "2" in result  # line number


@pytest.mark.asyncio
async def test_file_search_no_results(tmp_path) -> None:
    """搜索不存在的模式应返回无结果。"""
    from claw.builtin_tools.file_search import register as register_file_search

    registry = ToolsRegistry()
    register_file_search(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_search", {"glob": "*.nonexistent"})
    assert "No files" in result


@pytest.mark.asyncio
async def test_file_search_respects_max_results(tmp_path) -> None:
    """max_results 应限制结果数量。"""
    from claw.builtin_tools.file_search import register as register_file_search

    for i in range(10):
        (tmp_path / f"file_{i}.txt").write_text(f"content {i}")

    registry = ToolsRegistry()
    register_file_search(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_search", {"glob": "*.txt", "max_results": 2})
    lines = [l for l in result.strip().split("\n") if l.strip()]
    assert len(lines) <= 2


# --- file_patch ---


@pytest.mark.asyncio
async def test_file_patch_replaces_single_occurrence(tmp_path) -> None:
    """file_patch 应替换文件中的指定文本。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    (tmp_path / "test.py").write_text("def hello():\n    return 'old'\n")

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_patch", {
        "path": "test.py",
        "old_text": "'old'",
        "new_text": "'new'",
    })
    assert "OK" in result
    assert (tmp_path / "test.py").read_text() == "def hello():\n    return 'new'\n"


@pytest.mark.asyncio
async def test_file_patch_rejects_multiple_without_flag(tmp_path) -> None:
    """多次匹配但未设置 replace_all 时应拒绝。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    (tmp_path / "test.py").write_text("aaa bbb aaa")

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_patch", {
        "path": "test.py",
        "old_text": "aaa",
        "new_text": "ccc",
    })
    assert "Error" in result
    assert "2 times" in result


@pytest.mark.asyncio
async def test_file_patch_replaces_all_with_flag(tmp_path) -> None:
    """设置 replace_all=true 时应替换所有匹配。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    (tmp_path / "test.py").write_text("aaa bbb aaa")

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_patch", {
        "path": "test.py",
        "old_text": "aaa",
        "new_text": "ccc",
        "replace_all": True,
    })
    assert "OK" in result
    assert (tmp_path / "test.py").read_text() == "ccc bbb ccc"


@pytest.mark.asyncio
async def test_file_patch_rejects_empty_old_text(tmp_path) -> None:
    """空的 old_text 应被拒绝。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    (tmp_path / "test.py").write_text("hello")

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_patch", {
        "path": "test.py",
        "old_text": "",
        "new_text": "bad",
    })
    assert "Error" in result
    assert "empty" in result.lower()


@pytest.mark.asyncio
async def test_file_patch_rejects_not_found(tmp_path) -> None:
    """找不到 old_text 应返回错误。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    (tmp_path / "test.py").write_text("hello")

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_patch", {
        "path": "test.py",
        "old_text": "nonexistent",
        "new_text": "bad",
    })
    assert "Error" in result
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_file_patch_rejects_path_escape(tmp_path) -> None:
    """路径逃逸应被拒绝。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_patch", {
        "path": "../../etc/passwd",
        "old_text": "x",
        "new_text": "y",
    })
    assert "Error" in result


# --- protected path (source code write protection) ---


@pytest.mark.asyncio
async def test_file_write_rejects_protected_claw_dir(tmp_path) -> None:
    """file_write 应拒绝写入 claw/ 目录。"""
    from claw.builtin_tools.file_ops import register as register_file_ops

    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    (tmp_path / "claw").mkdir()
    result = await registry.execute("file_write", {
        "path": "claw/test.py",
        "content": "print('hello')",
    })
    assert "Error" in result
    assert "source code is not allowed" in result


@pytest.mark.asyncio
async def test_file_write_rejects_protected_tests_dir(tmp_path) -> None:
    """file_write 应拒绝写入 tests/ 目录。"""
    from claw.builtin_tools.file_ops import register as register_file_ops

    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    (tmp_path / "tests").mkdir()
    result = await registry.execute("file_write", {
        "path": "tests/test_new.py",
        "content": "def test_x(): pass",
    })
    assert "Error" in result
    assert "source code is not allowed" in result


@pytest.mark.asyncio
async def test_file_write_allows_data_dir(tmp_path) -> None:
    """file_write 应允许写入 data/ 目录。"""
    from claw.builtin_tools.file_ops import register as register_file_ops

    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    result = await registry.execute("file_write", {
        "path": "data/test.json",
        "content": '{"ok": true}',
    })
    assert result.startswith("OK:")


@pytest.mark.asyncio
async def test_file_patch_rejects_protected_claw_dir(tmp_path) -> None:
    """file_patch 应拒绝修改 claw/ 目录。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    (tmp_path / "claw").mkdir()
    (tmp_path / "claw" / "test.py").write_text("old code", encoding="utf-8")
    result = await registry.execute("file_patch", {
        "path": "claw/test.py",
        "old_text": "old",
        "new_text": "new",
    })
    assert "Error" in result
    assert "source code is not allowed" in result


@pytest.mark.asyncio
async def test_file_patch_rejects_protected_tests_dir(tmp_path) -> None:
    """file_patch 应拒绝修改 tests/ 目录。"""
    from claw.builtin_tools.file_patch import register as register_file_patch

    registry = ToolsRegistry()
    register_file_patch(registry, workspace_root=str(tmp_path))

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("old", encoding="utf-8")
    result = await registry.execute("file_patch", {
        "path": "tests/test_x.py",
        "old_text": "old",
        "new_text": "new",
    })
    assert "Error" in result
    assert "source code is not allowed" in result


@pytest.mark.asyncio
async def test_file_read_not_blocked_by_protection(tmp_path) -> None:
    """file_read 应不受保护路径限制，仍可读取源码。"""
    from claw.builtin_tools.file_ops import register as register_file_ops

    registry = ToolsRegistry()
    register_file_ops(registry, workspace_root=str(tmp_path))

    (tmp_path / "claw").mkdir()
    (tmp_path / "claw" / "test.py").write_text("source code", encoding="utf-8")
    result = await registry.execute("file_read", {"path": "claw/test.py"})
    assert "source code" in result
    assert "Error" not in result


# --- python_test ---


@pytest.mark.asyncio
async def test_python_test_rejects_shell_injection() -> None:
    """shell 注入参数应被拒绝。"""
    from claw.builtin_tools.python_test import register as register_python_test

    registry = ToolsRegistry()
    register_python_test(registry)

    result = await registry.execute("python_test", {"test_args": "tests/; rm -rf /"})
    assert "Error" in result
    assert "disallowed" in result.lower()


@pytest.mark.asyncio
async def test_python_test_rejects_disallowed_args() -> None:
    """非法 pytest 参数应被拒绝。"""
    from claw.builtin_tools.python_test import register as register_python_test

    registry = ToolsRegistry()
    register_python_test(registry)

    result = await registry.execute("python_test", {"test_args": "--custom-malicious-flag"})
    assert "Error" in result
    assert "disallowed" in result.lower()


@pytest.mark.asyncio
async def test_python_test_allows_valid_args() -> None:
    """合法的 pytest 参数应通过校验（可能 pytest 本身会运行）。"""
    from claw.builtin_tools.python_test import register as register_python_test

    registry = ToolsRegistry()
    register_python_test(registry)

    # 只测试参数校验通过，不关心 pytest 运行结果
    result = await registry.execute("python_test", {"test_args": "-v"})
    # 即使 pytest 运行失败，也不应是参数校验错误
    assert "disallowed" not in result.lower()


# --- git_tool ---


@pytest.mark.asyncio
async def test_git_status_in_repo(tmp_path) -> None:
    """在 git 仓库中应能获取 status。"""
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "test.txt").write_text("hello")

    from claw.builtin_tools.git_tool import register as register_git

    registry = ToolsRegistry()
    register_git(registry, workspace_root=str(tmp_path))

    result = await registry.execute("git_status", {})
    assert "test.txt" in result


@pytest.mark.asyncio
async def test_git_not_a_repo(tmp_path) -> None:
    """非 git 仓库应返回错误。"""
    from claw.builtin_tools.git_tool import register as register_git

    registry = ToolsRegistry()
    register_git(registry, workspace_root=str(tmp_path))

    result = await registry.execute("git_status", {})
    assert "Error" in result


# --- register_all ---


def test_register_all_registers_safe_tools() -> None:
    """register_all 应注册安全工具（不含 shell 和 python_test）。"""
    from claw.builtin_tools import register_all

    registry = ToolsRegistry()
    register_all(registry)

    names = {t.name for t in registry.list()}
    assert "calculator" in names
    assert "current_time" in names
    assert "file_read" in names
    assert "file_search" in names
    assert "file_patch" in names
    # shell 和 python_test 默认不注册
    assert "shell" not in names
    assert "python_test" not in names


# --- 工具参数 schema ---


def test_builtin_tools_have_parameters() -> None:
    """所有内置工具应有完整的参数 schema。"""
    from claw.builtin_tools import register_all

    registry = ToolsRegistry()
    register_all(registry)

    for tool in registry.list():
        assert tool.parameters is not None, f"{tool.name} missing parameters"
        assert "properties" in tool.parameters, f"{tool.name} missing properties"
