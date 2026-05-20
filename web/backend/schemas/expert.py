"""专家相关 Pydantic Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class ExpertMetaSchema(BaseModel):
    version: str = "0.1.0"
    author: str = ""
    tags: list[str] = []
    category: str = ""
    avatar: str = ""


class ExpertSchema(BaseModel):
    name: str
    display_name: str
    description: str
    system_prompt: str
    default_skills: list[str] = []
    default_tools: list[str] = []
    default_mcp_servers: list[str] = []
    default_model: dict = {}
    default_memory: dict = {}
    default_sandbox: dict = {}
    meta: ExpertMetaSchema = ExpertMetaSchema()
    source: str = "local"

    @classmethod
    def from_expert(cls, expert) -> ExpertSchema:
        return cls(
            name=expert.name,
            display_name=expert.display_name,
            description=expert.description,
            system_prompt=expert.system_prompt,
            default_skills=expert.default_skills,
            default_tools=expert.default_tools,
            default_mcp_servers=expert.default_mcp_servers,
            default_model=expert.default_model,
            default_memory=expert.default_memory,
            default_sandbox=expert.default_sandbox,
            meta=ExpertMetaSchema(
                version=expert.meta.version,
                author=expert.meta.author,
                tags=expert.meta.tags,
                category=expert.meta.category,
                avatar=expert.meta.avatar,
            ),
            source=expert.source,
        )


class ExpertListQuery(BaseModel):
    q: str = ""
    tag: str = ""
