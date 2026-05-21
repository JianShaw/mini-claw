"""技能相关 Pydantic Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class SkillMetaSchema(BaseModel):
    """技能元数据。"""
    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = []
    category: str = ""


class SkillListItemSchema(BaseModel):
    """技能列表条目（轻量，不含 instructions）。"""
    name: str
    description: str
    source: str = "local"
    version: str = "1.0.0"
    tools: list[str] = []
    category: str = ""

    @classmethod
    def from_skill(cls, skill) -> SkillListItemSchema:
        return cls(
            name=skill.name,
            description=skill.description,
            source=skill.source,
            version=skill.meta.version,
            tools=skill.tools,
            category=skill.meta.category,
        )


class SkillSchema(BaseModel):
    """技能完整详情（含 instructions）。"""
    name: str
    description: str
    instructions: str
    tools: list[str] = []
    meta: SkillMetaSchema = SkillMetaSchema()
    source: str = "local"
    path: str | None = None

    @classmethod
    def from_skill(cls, skill) -> SkillSchema:
        return cls(
            name=skill.name,
            description=skill.description,
            instructions=skill.instructions,
            tools=skill.tools,
            meta=SkillMetaSchema(
                version=skill.meta.version,
                author=skill.meta.author,
                tags=skill.meta.tags,
                category=skill.meta.category,
            ),
            source=skill.source,
            path=skill.path,
        )


class ExportRequestSchema(BaseModel):
    """批量导出请求体。"""
    names: list[str]
