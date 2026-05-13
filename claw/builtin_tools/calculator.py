"""安全数学计算工具：使用 ast 白名单求值，拒绝危险调用。"""

from __future__ import annotations

import ast
from typing import Any

from claw.tools import Tool, ToolsRegistry

# 允许的 AST 节点白名单
_ALLOWED_NODES = (
    ast.Expression, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
)


def _safe_eval(expression: str) -> str:
    """安全求值数学表达式，仅允许数字和基本运算符。"""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        return f"Syntax error: {e}"

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return f"Error: disallowed expression element in '{expression}'"

    try:
        result = eval(compile(tree, "<calc>", "eval"))  # noqa: S307
        return str(result)
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


async def _calculate(args: dict[str, Any]) -> str:
    return _safe_eval(args["expression"])


def register(registry: ToolsRegistry) -> None:
    registry.register(Tool(
        name="calculator",
        description="Evaluate a mathematical expression and return the result. Supports +, -, *, /, %, **, comparisons.",
        handler=_calculate,
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to evaluate, e.g. '2 + 3 * 4'",
                },
            },
            "required": ["expression"],
        },
    ))
