"""安全数学计算工具：使用 ast 白名单求值，拒绝危险调用和大指数 DoS。"""

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

# 安全限制：防止 CPU/内存 DoS
_MAX_EXPR_LENGTH = 200
_MAX_INT_VALUE = 10**15
_MAX_EXPONENT = 10000


def _safe_eval(expression: str) -> str:
    """安全求值数学表达式，仅允许数字和基本运算符。"""
    if len(expression) > _MAX_EXPR_LENGTH:
        return f"Error: expression too long (max {_MAX_EXPR_LENGTH} chars)"

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        return f"Syntax error: {e}"

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return f"Error: disallowed expression element in '{expression}'"
        # 限制整数字面量大小
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            if abs(node.value) > _MAX_INT_VALUE:
                return f"Error: integer value too large (max {_MAX_INT_VALUE})"
        # 限制指数大小
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, (int, float)):
                if abs(node.right.value) > _MAX_EXPONENT:
                    return f"Error: exponent too large (max {_MAX_EXPONENT})"

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
