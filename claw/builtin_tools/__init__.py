"""内置工具包：提供常用工具供 Agent 使用。

默认注册安全的工具（calculator、time）。危险工具（shell、file_ops、web）
需要显式 opt-in：通过各自的 register() 函数配置安全参数后手动注册。
"""

from __future__ import annotations

from claw.tools import ToolsRegistry


def register_all(registry: ToolsRegistry) -> None:
    """注册所有内置工具到指定注册表。

    安全工具默认注册；需要安全配置的工具使用默认安全参数。
    对于生产环境，建议手动注册并配置安全参数。
    """
    from claw.builtin_tools.calculator import register as register_calculator
    from claw.builtin_tools.file_ops import register as register_file_ops
    from claw.builtin_tools.time_tool import register as register_time

    # 安全工具，无风险
    register_calculator(registry)
    register_time(registry)

    # 文件操作：限定当前工作目录为 workspace root
    register_file_ops(registry)

    # Shell 工具：默认不注册，需手动注册并指定白名单
    # 如需启用：shell.register(registry, allowed_commands=["ls", "cat", "echo"])

    # web_search 可选依赖，仅在安装了 duckduckgo-search 时注册
    try:
        from claw.builtin_tools.web_search import register as register_web_search
        register_web_search(registry)
    except ImportError:
        pass
