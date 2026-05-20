"""技能注册表：管理技能的注册、查找、激活、搜索和持久化。

两层技能加载机制：
- Layer 1（系统提示）：build_skills_listing() 生成轻量级索引（name + description）
- Layer 2（tool_result）：get_skill_instructions() 返回完整指令，由 load_skill 工具调用

LLM 通过调用 load_skill("skill-name") 按需加载技能完整指令，
指令通过 tool_result 注入对话，不占用系统提示词空间。
"""

from __future__ import annotations

import logging
from typing import Any

from claw.skills.types import Skill

logger = logging.getLogger(__name__)


class SkillsRegistry:
    """技能注册表：注册、查找、列举、激活、搜索技能。

    职责边界：
    - 注册 / 注销技能（内存 + 可选持久化）
    - 按名称 / 标签搜索技能
    - 激活 / 停用技能（用于斜杠命令标记）
    - 生成轻量级技能列表供 LLM 浏览
    - 返回指定技能的完整指令（供 load_skill 工具调用）
    - 不负责：SKILL.md 解析（由 SkillLoader 负责）
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._active_skill: Skill | None = None
        self._store: Any = None

    # --- 基础操作 ---

    def register(self, skill: Skill) -> None:
        """注册技能，不允许重复同名。"""
        if skill.name in self._skills:
            raise ValueError(f"skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """按名称获取技能。"""
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        """列出所有已注册技能。"""
        return list(self._skills.values())

    def unregister(self, name: str) -> Skill:
        """注销技能，返回被移除的 Skill。不存在时抛出 KeyError。"""
        if name not in self._skills:
            raise KeyError(f"skill not found: {name}")
        skill = self._skills.pop(name)
        if self._active_skill and self._active_skill.name == name:
            self._active_skill = None
        return skill

    def update(self, skill: Skill) -> None:
        """更新已有技能。不存在时抛出 KeyError。"""
        if skill.name not in self._skills:
            raise KeyError(f"skill not found: {skill.name}")
        self._skills[skill.name] = skill
        if self._active_skill and self._active_skill.name == skill.name:
            self._active_skill = skill

    def search(self, query: str) -> list[Skill]:
        """模糊搜索：按名称、描述、标签匹配，不区分大小写。"""
        q = query.lower()
        results: list[Skill] = []
        for skill in self._skills.values():
            if (
                q in skill.name.lower()
                or q in skill.description.lower()
                or any(q in tag.lower() for tag in skill.meta.tags)
            ):
                results.append(skill)
        return results

    def get_by_slash_name(self, slash: str) -> Skill | None:
        """通过斜杠命令名（如 /code-review）查找技能。"""
        target = slash.lstrip("/").lower()
        for skill in self._skills.values():
            normalized = skill.name.replace("_", "-").replace(" ", "-").lower()
            if normalized == target:
                return skill
        return None

    # --- 激活管理（用于斜杠命令标记） ---

    def activate(self, name: str) -> Skill:
        """激活指定技能，同一时刻只能有一个激活。不存在时抛出 KeyError。"""
        if name not in self._skills:
            raise KeyError(f"skill not found: {name}")
        self._active_skill = self._skills[name]
        return self._active_skill

    def deactivate(self) -> None:
        """停用当前激活的技能。"""
        self._active_skill = None

    @property
    def active_skill(self) -> Skill | None:
        """获取当前激活的技能。"""
        return self._active_skill

    # --- 两层技能加载 ---

    def build_skills_listing(self, *, enabled_skills: list[str] | None = None) -> str:
        """生成轻量级技能索引，注入系统提示词（Layer 1）。

        仅包含 name + description，不包含完整指令。
        引导 LLM 通过 load_skill 工具按需加载完整指令。
        如果有活跃技能，会明确提示 LLM 加载它。

        enabled_skills: 允许的技能名列表，None 表示不过滤，空列表返回空字符串。
        """
        skills = list(self._skills.values())
        if enabled_skills is not None:
            skills = [s for s in skills if s.name in enabled_skills]
        if not skills:
            return ""
        lines = ["## Available Skills", ""]
        for skill in skills:
            tools = (
                f" (tools: {', '.join(skill.tools)})" if skill.tools else ""
            )
            marker = " *" if self._active_skill and self._active_skill.name == skill.name else ""
            lines.append(
                f"- **{skill.name}**{marker}: {skill.description}{tools}"
            )
        lines.append("")
        if self._active_skill:
            lines.append(
                f"The skill **{self._active_skill.name}** is currently active. "
                f"**You MUST call `load_skill` with name \"{self._active_skill.name}\" before responding** "
                f"to load its full workflow."
            )
        else:
            lines.append(
                "When the user's request matches a skill listed above, "
                "**you MUST call `load_skill` first** to load the skill's full instructions, "
                "then follow those instructions to respond."
            )
        return "\n".join(lines)

    def get_skill_instructions(self, name: str) -> str | None:
        """返回指定技能的完整指令（Layer 2，供 load_skill 工具调用）。

        技能不存在时返回 None。
        """
        skill = self._skills.get(name)
        if skill is None:
            return None
        parts = [f'<skill name="{skill.name}">', skill.instructions]
        if skill.tools:
            parts.append(f"Available tools: {', '.join(skill.tools)}")
        parts.append("</skill>")
        return "\n".join(parts)

    # --- 持久化 ---

    async def load_from_store(self) -> None:
        """从文件存储加载所有技能。需要先调用 set_store() 设置存储。"""
        if self._store is None:
            return
        skills = self._store.load_all()
        self._skills.clear()
        for skill in skills:
            self._skills[skill.name] = skill
        logger.info("从存储加载了 %d 个技能", len(skills))

    async def reload(self) -> None:
        """重新从存储加载技能。"""
        await self.load_from_store()

    def set_store(self, store: Any) -> None:
        """设置底层存储（SkillStore 实例）。"""
        self._store = store
