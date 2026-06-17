"""文件搜索工具：按 glob 模式和关键字搜索 sandbox 文件。

安全策略：
- glob 搜索基于 sandbox_root，结果天然限定在 sandbox 内
- keyword 搜索仅在 sandbox 内的文件中检索
- 结果数量受 max_results 限制
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claw.tools import Tool, ToolsRegistry


def register(
    registry: ToolsRegistry,
    *,
    sandbox_root: str | None = None,
) -> None:
    """注册文件搜索工具。

    Args:
        registry: 工具注册表
        sandbox_root: 搜索的根目录边界，默认为当前工作目录。
            运行时可通过 args["_sandbox_root"] 覆盖（由 AgentRunner 注入）。
    """
    root = Path(sandbox_root or ".").resolve()

    async def handler(args: dict[str, Any]) -> str:
        # 运行时 sandbox 优先于注册时默认值
        dynamic = args.get("_sandbox_root")
        ws = Path(dynamic).resolve() if dynamic else root

        pattern = args.get("glob", "*")
        keyword = args.get("keyword")
        max_results = args.get("max_results", 50)

        # glob 直接在 ws 上执行，结果天然在 sandbox 内
        try:
            matches = sorted(ws.glob(pattern))
        except Exception as e:
            return f"Error: invalid glob pattern: {e}"

        if not matches:
            return "No files matched."

        lines: list[str] = []
        for match in matches[:max_results]:
            try:
                rel = match.relative_to(ws)
            except ValueError:
                continue

            if keyword and match.is_file():
                # keyword 搜索：在文件内容中查找关键字，返回行号
                try:
                    text = match.read_text(encoding="utf-8", errors="ignore")
                    for i, line in enumerate(text.splitlines(), 1):
                        if keyword.lower() in line.lower():
                            lines.append(f"{rel}:{i}: {line.strip()}")
                            if len(lines) >= max_results:
                                break
                except Exception:
                    lines.append(f"{rel}: (unreadable)")
            else:
                lines.append(str(rel))

            if len(lines) >= max_results:
                break

        return "\n".join(lines) if lines else "No matches found."

    registry.register(Tool(
        name="file_search",
        description="Search for files in sandbox by glob pattern and optionally by keyword content. Returns matching file paths and line numbers.",
        handler=handler,
        parameters={
            "type": "object",
            "properties": {
                "glob": {"type": "string", "description": "Glob pattern to match files (default '*')"},
                "keyword": {"type": "string", "description": "Optional keyword to search within file contents"},
                "max_results": {"type": "integer", "description": "Maximum number of results (default 50)"},
            },
            "required": [],
        },
    ))
