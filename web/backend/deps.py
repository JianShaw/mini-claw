"""FastAPI 依赖注入：管理 SQLite 连接、Store、Service 的生命周期。"""

from __future__ import annotations

from functools import lru_cache

from claw.agent_runtime.factory import AgentFactory
from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.expert.marketplace import ExpertMarketplace
from claw.expert.registry import ExpertRegistry
from claw.expert.service import ExpertService
from claw.expert.store import SqliteExpertStore
from claw.storage.sqlite import get_connection, init_db


@lru_cache(maxsize=1)
def _get_connection():
    """单例 SQLite 连接。"""
    conn = get_connection()
    init_db(conn)
    return conn


def get_expert_store() -> SqliteExpertStore:
    return SqliteExpertStore(_get_connection())


def get_agent_store() -> SqliteAgentStore:
    return SqliteAgentStore(_get_connection())


def get_expert_service() -> ExpertService:
    return ExpertService(get_expert_store())


def get_expert_registry() -> ExpertRegistry:
    return ExpertRegistry(get_expert_store())


def get_expert_marketplace() -> ExpertMarketplace:
    return ExpertMarketplace(get_expert_store())


def get_agent_factory() -> AgentFactory:
    return AgentFactory(get_expert_store(), get_agent_store())


def get_agent_resolver() -> AgentResolver:
    return AgentResolver(get_agent_store())
