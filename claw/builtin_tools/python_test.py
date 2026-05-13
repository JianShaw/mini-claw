"""Python 测试运行工具：在 workspace 内执行白名单 pytest 命令。

安全策略：
- 使用 create_subprocess_exec 避免注入
- 每个 pytest 参数都用正则白名单校验
- 超时控制防止挂起
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from claw.tools import Tool, ToolsRegistry

# 允许的 pytest 参数模式（按优先级匹配：具体模式在前，宽泛路径在后）
_ALLOWED_TEST_PATTERNS = [
    re.compile(r"^\w+::\w+::\w+$"),    # module::class::test_name
    re.compile(r"^\w+::\w+$"),         # module::test_name
    re.compile(r"^-v+$"),              # 详细度标志
    re.compile(r"^--tb=\w+$"),         # traceback 格式
    re.compile(r"^-x$"),               # 首个失败后停止
    re.compile(r"^-q$"),               # 安静模式
    re.compile(r"^-s$"),               # 不捕获输出
    re.compile(r"^[^\-][\w./\\:_-]*$"),  # 文件路径（不以 - 开头）
]


def _validate_test_args(args_str: str) -> str | None:
    """校验 pytest 参数是否在白名单内。返回错误字符串或 None（通过）。"""
    if not args_str.strip():
        return None
    parts = args_str.strip().split()
    for part in parts:
        if not any(p.match(part) for p in _ALLOWED_TEST_PATTERNS):
            return f"Error: disallowed test argument: '{part}'"
    return None


def register(
    registry: ToolsRegistry,
    *,
    workspace_root: str | None = None,
    default_timeout: int = 120,
) -> None:
    """注册 Python 测试运行工具。

    Args:
        registry: 工具注册表
        workspace_root: 工作目录，pytest 在此目录下运行
        default_timeout: 默认超时秒数
    """
    root = Path(workspace_root or ".").resolve()

    async def handler(args: dict[str, Any]) -> str:
        test_args = args.get("test_args", "")
        cmd_timeout = args.get("timeout", default_timeout)

        validation = _validate_test_args(test_args)
        if validation:
            return validation

        cmd = ["python", "-m", "pytest"]
        if test_args.strip():
            cmd.extend(test_args.strip().split())

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(root),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=cmd_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: test timed out after {cmd_timeout}s"
        except FileNotFoundError:
            return "Error: python or pytest not found"

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        parts = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(stderr)
        parts.append(f"exit code: {proc.returncode}")
        return "\n".join(parts)

    registry.register(Tool(
        name="python_test",
        description="Run pytest test commands within the workspace. Only whitelisted test arguments are allowed.",
        handler=handler,
        parameters={
            "type": "object",
            "properties": {
                "test_args": {
                    "type": "string",
                    "description": "Pytest arguments, e.g. 'tests/test_foo.py::test_bar -v'",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120)",
                },
            },
            "required": [],
        },
    ))
