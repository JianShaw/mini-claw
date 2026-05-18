"""文件补丁工具：精确替换文件中的文本块，比全量 file_write 更安全。

安全策略：
- 复用 file_ops 的 _safe_path 和 _contains_symlink 做路径和 symlink 校验
- old_text 不能为空，防止误操作
- 多次匹配时默认拒绝，需显式设置 replace_all=true
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claw.builtin_tools.file_ops import _contains_symlink, _is_protected_path, _safe_path
from claw.tools import Tool, ToolsRegistry


def register(
    registry: ToolsRegistry,
    *,
    workspace_root: str | None = None,
) -> None:
    """注册文件补丁工具。

    Args:
        registry: 工具注册表
        workspace_root: 文件操作的根目录边界，默认为当前工作目录
    """
    root = Path(workspace_root or ".").resolve()

    async def handler(args: dict[str, Any]) -> str:
        result = _safe_path(args["path"], root)
        if isinstance(result, str):
            return result
        path = result

        old_text = args["old_text"]
        new_text = args["new_text"]
        replace_all = args.get("replace_all", False)

        if _contains_symlink(path, root):
            return f"Error: symlink not allowed: {path}"

        if _is_protected_path(path, root):
            return f"Error: modifying source code is not allowed: {path.relative_to(root)}"

        if not old_text:
            return "Error: old_text cannot be empty"

        try:
            if not path.exists():
                return f"Error: file not found: {path.relative_to(root)}"
            content = path.read_text(encoding="utf-8")

            count = content.count(old_text)
            if count == 0:
                return f"Error: old_text not found in {path.relative_to(root)}"
            if count > 1 and not replace_all:
                return f"Error: old_text found {count} times; set replace_all=true to replace all"

            if replace_all:
                new_content = content.replace(old_text, new_text)
            else:
                new_content = content.replace(old_text, new_text, 1)

            path.write_text(new_content, encoding="utf-8")
            return f"OK: replaced {count if replace_all else 1} occurrence(s) in {path.relative_to(root)}"

        except PermissionError:
            return f"Error: permission denied: {path}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    registry.register(Tool(
        name="file_patch",
        description="Replace a specific text block in a file. Safer than full file_write as it only modifies the matched section.",
        handler=handler,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root"},
                "old_text": {"type": "string", "description": "Exact text to find and replace"},
                "new_text": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    ))
