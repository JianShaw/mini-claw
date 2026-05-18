"""技能数据模型：Skill、SkillMeta 等核心类型定义。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkillMeta:
    """技能元数据：版本、作者、标签、分类等。"""

    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# 合法的技能名称：小写字母数字 + 连字符，≤64 字符
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(slots=True)
class Skill:
    """技能定义：名称、描述、指令模板、绑定的工具列表。

    技能不执行代码——它们通过 instructions 注入到系统提示词中，
    指导 LLM 按照预定义的工作流程编排已有工具。

    文件格式：SKILL.md（YAML frontmatter + Markdown body），
    对齐 OpenClaw 的技能文件格式。
    """

    name: str
    description: str
    instructions: str
    tools: list[str] = field(default_factory=list)
    meta: SkillMeta = field(default_factory=SkillMeta)
    # 来源：bundled（内置）/ local（用户安装）/ imported（导入）
    source: str = "local"
    # 技能文件的相对路径（由 loader 设置）
    path: str | None = None

    def __post_init__(self) -> None:
        """构造时校验名称合法性，防止路径注入。"""
        if not self.is_valid_name(self.name):
            raise ValueError(
                f"Invalid skill name: '{self.name}' "
                "(must be lowercase alphanumeric + hyphens, 1-64 chars)"
            )

    @property
    def slash_name(self) -> str:
        """斜杠命令名：直接返回 /{name}。"""
        return f"/{self.name}"

    @staticmethod
    def is_valid_name(name: str) -> bool:
        """检查技能名称是否合法：小写字母数字 + 连字符，1-64 字符。"""
        return bool(_VALID_NAME_RE.match(name))


class SkillLoadError(Exception):
    """技能加载失败时的异常。"""
