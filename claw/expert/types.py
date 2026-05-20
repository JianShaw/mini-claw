"""专家模板数据类型定义。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ExpertMeta:
    """专家元数据：版本、作者、标签、分类等。"""

    version: str = "0.1.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = ""
    avatar: str = ""               # emoji 或 URL
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Expert:
    """专家模板：定义一个可安装的专家配置。

    name 是唯一标识，display_name 是 UI 显示名称。
    system_prompt 是 EXPERT.md body 部分，作为 Agent 的默认系统提示词。
    """

    name: str                      # 唯一标识（小写字母数字+连字符）
    display_name: str              # 显示名称
    description: str               # 简短描述
    system_prompt: str             # EXPERT.md body — 默认系统提示词
    default_skills: list[str] = field(default_factory=list)
    default_tools: list[str] = field(default_factory=list)
    default_mcp_servers: list[str] = field(default_factory=list)
    default_model: dict[str, Any] = field(default_factory=dict)
    default_memory: dict[str, Any] = field(default_factory=dict)
    default_sandbox: dict[str, Any] = field(default_factory=dict)
    meta: ExpertMeta = field(default_factory=ExpertMeta)
    source: str = "local"          # "bundled" | "local"
    path: str | None = None

    _VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

    @classmethod
    def is_valid_name(cls, name: str) -> bool:
        """校验专家名称：小写字母开头，仅含小写字母/数字/连字符，1-64字符。"""
        return bool(cls._VALID_NAME_RE.match(name))
