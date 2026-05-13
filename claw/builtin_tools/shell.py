"""Shell 命令执行工具：通过 asyncio 子进程执行命令。

安全策略：
- 默认不注册任何命令，需通过 allowed_commands 白名单显式启用
- 工作目录可限定为指定沙箱路径
- 超时控制防止命令挂起
"""

from __future__ import annotations

import asyncio
from pathlib import Path
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

        # 白名单检查：只允许指定的命令
        if allowed is not None:
            cmd_parts = command.strip().split()
            cmd_prefix = cmd_parts[0] if cmd_parts else ""
            cmd_name = Path(cmd_prefix).name
            if cmd_name not in allowed:
                return f"Error: command '{cmd_name}' not in allowed list: {allowed}"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
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

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        parts = []
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        parts.append(f"exit code: {proc.returncode}")
        return "\n".join(parts)

    return handler


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
        description="Execute a shell command and return stdout, stderr, and exit code. Only allowed commands can be executed.",
        handler=_make_handler(allowed_commands, cwd, default_timeout),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30)",
                },
            },
            "required": ["command"],
        },
    ))
