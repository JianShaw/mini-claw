"""内置工具包：提供常用工具供 Agent 使用。

安全工具默认注册（calculator、time、file_ops、file_search、file_patch）。
危险工具（shell、python_test）需要显式 opt-in。
Git 工具自动检测是否在 git 仓库中。
web_search 为可选依赖。
"""

from __future__ import annotations

from claw.tools import ToolsRegistry


def register_all(registry: ToolsRegistry, *, skills_registry: Any = None) -> None:
    """注册所有内置工具到指定注册表。

    安全工具默认注册；需要安全配置的工具使用默认安全参数。
    对于生产环境，建议手动注册并配置安全参数。

    Args:
        registry: 工具注册表
        skills_registry: 可选的 SkillsRegistry 实例，传入时注册 load_skill 工具
    """
    from claw.builtin_tools.calculator import register as register_calculator
    from claw.builtin_tools.file_ops import register as register_file_ops
    from claw.builtin_tools.file_patch import register as register_file_patch
    from claw.builtin_tools.file_search import register as register_file_search
    from claw.builtin_tools.time_tool import register as register_time

    # 安全工具，无风险
    register_calculator(registry)
    register_time(registry)

    # 文件操作：限定当前工作目录为 workspace root
    register_file_ops(registry)
    register_file_search(registry)
    register_file_patch(registry)

    # Shell 工具：注册并指定白名单
    # python_test：默认不注册，需手动注册
    # 如需启用：python_test.register(registry)

    # Git 工具：自动检测是否在 git 仓库中
    try:
        from claw.builtin_tools.git_tool import register as register_git
        register_git(registry)
    except Exception:
        pass

    # web_search 可选依赖，仅在安装了 duckduckgo-search 时注册
    try:
        from claw.builtin_tools.web_search import register as register_web_search
        register_web_search(registry)
    except ImportError:
        pass

    # 技能加载工具：需要 skills_registry 才能注册
    if skills_registry is not None:
        from claw.builtin_tools.skill_loader import register as register_skill_loader
        register_skill_loader(registry, skills_registry)
