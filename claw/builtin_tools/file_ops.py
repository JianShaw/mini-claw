"""文件操作工具：读取、写入、列出文件和目录。

安全策略：
- 所有操作限定在 workspace_root 目录内
- 解析路径后检查是否逃逸 workspace
- 拒绝符号链接逃逸
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claw.tools import Tool, ToolsRegistry


def _safe_path(path_str: str, workspace_root: Path) -> Path | str:
    """解析路径并检查是否在 workspace_root 内。返回 Path 或错误字符串。

    使用 relative_to 做路径分量级别的检查，避免字符串前缀绕过
    （例如 root=/tmp/app 时 /tmp/app2/evil 会被正确拒绝）。
    """
    target = (workspace_root / path_str).resolve()
    root = workspace_root.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return f"Error: path escapes workspace root: {path_str}"
    return target


def _contains_symlink(path: Path, root: Path) -> bool:
    """检查路径本身或其父目录链（到 root 为止）是否包含符号链接。"""
    if path.is_symlink():
        return True
    root_resolved = root.resolve()
    current = path.parent
    while current != current.parent and current != root_resolved:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def register(
    registry: ToolsRegistry,
    *,
    workspace_root: str | None = None,
) -> None:
    """注册文件操作工具。

    Args:
        registry: 工具注册表
        workspace_root: 文件操作的根目录边界，默认为当前工作目录
    """
    root = Path(workspace_root or ".").resolve()

    def _file_read_handler(args: dict[str, Any]) -> str:
        result = _safe_path(args["path"], root)
        if isinstance(result, str):
            return result
        path = result
        encoding = args.get("encoding", "utf-8")
        try:
            if _contains_symlink(path, root):
                return f"Error: symlink not allowed: {path}"
            content = path.read_text(encoding=encoding)
            if len(content) > 1_000_000:
                return content[:1_000_000] + "\n... (truncated, file too large)"
            return content
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        except PermissionError:
            return f"Error: permission denied: {path}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    async def _file_read_async(args: dict[str, Any]) -> str:
        return _file_read_handler(args)

    def _file_write_handler(args: dict[str, Any]) -> str:
        result = _safe_path(args["path"], root)
        if isinstance(result, str):
            return result
        path = result
        if _contains_symlink(path, root):
            return f"Error: symlink not allowed: {path}"
        content = args["content"]
        encoding = args.get("encoding", "utf-8")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)
            return f"OK: wrote {len(content)} chars to {path.relative_to(root)}"
        except PermissionError:
            return f"Error: permission denied: {path}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    async def _file_write_async(args: dict[str, Any]) -> str:
        return _file_write_handler(args)

    def _file_list_handler(args: dict[str, Any]) -> str:
        dir_path = args.get("path", ".")
        result = _safe_path(dir_path, root)
        if isinstance(result, str):
            return result
        path = result
        try:
            if not path.is_dir():
                return f"Error: not a directory: {path.relative_to(root)}"
            entries = sorted(path.iterdir())
            lines = []
            for entry in entries:
                # 跳过符号链接
                if entry.is_symlink():
                    lines.append(f"[LINK] {entry.name}")
                elif entry.is_dir():
                    lines.append(f"[DIR]  {entry.name}/")
                else:
                    size = entry.stat().st_size
                    lines.append(f"[FILE] {entry.name} ({size} bytes)")
            return "\n".join(lines) if lines else "(empty directory)"
        except FileNotFoundError:
            return f"Error: directory not found: {path}"
        except PermissionError:
            return f"Error: permission denied: {path}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    async def _file_list_async(args: dict[str, Any]) -> str:
        return _file_list_handler(args)

    registry.register(Tool(
        name="file_read",
        description="Read the contents of a text file. Restricted to workspace root directory.",
        handler=_file_read_async,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace root"},
                "encoding": {"type": "string", "description": "File encoding (default utf-8)"},
            },
            "required": ["path"],
        },
    ))
    registry.register(Tool(
        name="file_write",
        description="Write text content to a file. Restricted to workspace root directory. Creates parent directories if needed.",
        handler=_file_write_async,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace root"},
                "content": {"type": "string", "description": "Text content to write"},
                "encoding": {"type": "string", "description": "File encoding (default utf-8)"},
            },
            "required": ["path", "content"],
        },
    ))
    registry.register(Tool(
        name="file_list",
        description="List files and directories in a given path. Restricted to workspace root directory.",
        handler=_file_list_async,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to workspace root (default current directory)"},
            },
            "required": [],
        },
    ))
