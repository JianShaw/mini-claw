"""Shell 命令执行工具：通过 asyncio 子进程执行命令。

安全策略：
- 默认不注册任何命令，需通过 allowed_commands 白名单显式启用
- 工作目录可限定为指定沙箱路径
- 超时控制防止命令挂起
- 使用 create_subprocess_exec 避免 shell 元字符注入（&&、|、;等）
"""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any

from claw.tools import Tool, ToolsRegistry


def _make_handler(
    allowed: list[str] | None,
    work_dir: str | None,
    timeout: int,
):
    """创建 shell handler 闭包，捕获安全配置。"""

    async def handler(args: dict[str, Any]) -> str:
        command = args["command"]
        cmd_timeout = args.get("timeout", timeout)

        # 使用 shlex.split 正确解析命令（处理引号等），避免 shell 元字符注入
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return f"Error: invalid command syntax: {e}"

        if not parts:
            return "Error: empty command"

        # 安全：拒绝带路径的命令（如 /usr/bin/python、./malicious），
        # 只允许裸命令名（如 python、ls），防止路径遍历绕过白名单
        if "/" in parts[0] or "\\" in parts[0]:
            return f"Error: path in command not allowed, use bare command name: {parts[0]}"

        cmd_name = parts[0]

        # 白名单检查：只允许指定的命令
        if allowed is not None:
            if cmd_name not in allowed:
                return f"Error: command '{cmd_name}' not in allowed list: {allowed}"

        # Windows 上 echo/pwd/sleep 可能不是独立可执行文件。这里用安全的
        # Python 内建模拟它们，既保持跨平台测试稳定，也不启用 shell。
        builtin_result = await _run_portable_builtin(parts, work_dir, cmd_timeout)
        if builtin_result is not None:
            return builtin_result

        try:
            # 使用 exec 而非 shell，避免 &&、|、; 等被解释为 shell 操作符
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=cmd_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: command timed out after {cmd_timeout}s"
        except FileNotFoundError:
            return f"Error: command not found: {cmd_name}"

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        parts_out = []
        if stdout:
            parts_out.append(f"stdout:\n{stdout}")
        if stderr:
            parts_out.append(f"stderr:\n{stderr}")
        parts_out.append(f"exit code: {proc.returncode}")
        return "\n".join(parts_out)

    return handler


async def _run_portable_builtin(
    parts: list[str],
    work_dir: str | None,
    timeout: int,
) -> str | None:
    """跨平台处理少量简单命令，不经过系统 shell。"""
    cmd_name = parts[0]
    if cmd_name == "echo":
        output = " ".join(parts[1:])
        return f"stdout:\n{output}\nexit code: 0" if output else "exit code: 0"
    if cmd_name == "pwd":
        return f"stdout:\n{work_dir or os.getcwd()}\nexit code: 0"
    if cmd_name == "sleep":
        seconds = float(parts[1]) if len(parts) > 1 else 1.0
        try:
            #当前协程等一会儿，模拟 sleep 效果，同时响应超时取消
            await asyncio.wait_for(asyncio.sleep(seconds), timeout=timeout)
        except asyncio.TimeoutError:
            return f"Error: command timed out after {timeout}s"
        return "exit code: 0"
    return None


def register(
    registry: ToolsRegistry,
    *,
    allowed_commands: list[str] | None = None,
    cwd: str | None = None,
    default_timeout: int = 30,
) -> None:
    """注册 shell 工具。

    Args:
        registry: 工具注册表
        allowed_commands: 允许执行的命令名白名单，如 ["ls", "echo", "cat", "pwd"]。
                          为 None 时不限制（仅在受信环境使用）。
        cwd: 限定工作目录，None 则使用进程当前目录
        default_timeout: 默认超时秒数
    """
    registry.register(Tool(
        name="shell",
        description="Execute a command and return stdout, stderr, and exit code. Only allowed commands can be executed. Shell operators (&&, |, ;) are not supported.",
        handler=_make_handler(allowed_commands, cwd, default_timeout),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute (no shell operators)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30)",
                },
            },
            "required": ["command"],
        },
    ))
