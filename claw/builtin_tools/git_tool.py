"""Git 只读工具：提供 status、diff、log、branch 等只读 git 操作。

安全策略：
- 所有 git 命令硬编码在字典中，不接受用户拼接参数
- 使用 create_subprocess_exec 避免 shell 注入
- 所有操作均为只读
"""

from __future__ import annotations

import asyncio
from typing import Any

from claw.tools import Tool, ToolsRegistry

# 硬编码的只读 git 命令，不允许用户自定义
_GIT_COMMANDS = {
    "status": ["git", "status", "--porcelain"],
    "diff": ["git", "diff"],
    "diff_staged": ["git", "diff", "--cached"],
    "log": ["git", "log", "--oneline", "-10"],
    "branch": ["git", "branch", "--list"],
}


async def _git_command(
    command: str,
    *,
    cwd: str,
    timeout: int = 10,
) -> str:
    """执行白名单中的 git 命令，返回输出字符串。"""
    if command not in _GIT_COMMANDS:
        return f"Error: unknown git command '{command}'. Allowed: {list(_GIT_COMMANDS.keys())}"

    cmd = _GIT_COMMANDS[command]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        return f"Error: git command timed out after {timeout}s"
    except FileNotFoundError:
        return "Error: git not found"

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        return f"Error: {stderr or 'git command failed'}"

    return stdout or "(no output)"


def register(
    registry: ToolsRegistry,
    *,
    workspace_root: str | None = None,
    default_timeout: int = 10,
) -> None:
    """注册 Git 只读工具。

    Args:
        registry: 工具注册表
        workspace_root: git 仓库根目录
        default_timeout: 默认超时秒数
    """
    cwd = workspace_root or "."

    async def git_status_handler(args: dict[str, Any]) -> str:
        return await _git_command("status", cwd=cwd, timeout=default_timeout)

    async def git_diff_handler(args: dict[str, Any]) -> str:
        staged = args.get("staged", False)
        cmd = "diff_staged" if staged else "diff"
        return await _git_command(cmd, cwd=cwd, timeout=default_timeout)

    async def git_log_handler(args: dict[str, Any]) -> str:
        return await _git_command("log", cwd=cwd, timeout=default_timeout)

    async def git_branch_handler(args: dict[str, Any]) -> str:
        return await _git_command("branch", cwd=cwd, timeout=default_timeout)

    registry.register(Tool(
        name="git_status",
        description="Show git working tree status (porcelain format). Read-only.",
        handler=git_status_handler,
        parameters={"type": "object", "properties": {}, "required": []},
    ))
    registry.register(Tool(
        name="git_diff",
        description="Show git diff of changes. Read-only. Use staged=true for --cached.",
        handler=git_diff_handler,
        parameters={
            "type": "object",
            "properties": {
                "staged": {"type": "boolean", "description": "Show staged changes (--cached)"},
            },
            "required": [],
        },
    ))
    registry.register(Tool(
        name="git_log",
        description="Show recent git log (last 10 commits, oneline format). Read-only.",
        handler=git_log_handler,
        parameters={"type": "object", "properties": {}, "required": []},
    ))
    registry.register(Tool(
        name="git_branch",
        description="List local git branches. Read-only.",
        handler=git_branch_handler,
        parameters={"type": "object", "properties": {}, "required": []},
    ))
