"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from claw.agent_runtime.factory import AgentFactory
from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.expert.registry import ExpertRegistry
from claw.expert.marketplace import ExpertMarketplace
from claw.expert.service import ExpertService
from claw.expert.store import SqliteExpertStore
from claw.gateway import RuntimeGateway
from claw.scheduler.agent_run import AgentRunService
from claw.storage.session_store import SqliteSessionStore
from claw.storage.sqlite import get_connection, init_db
from web.backend.routers import agents, chat, conversations, experts, tasks
from web.backend.services.task_service import TaskService


def create_app(
    *,
    gateway: RuntimeGateway | None = None,
    expert_store: SqliteExpertStore | None = None,
    agent_store: SqliteAgentStore | None = None,
    db_path: str = "data/mini_claw.sqlite",
) -> FastAPI:
    """创建 FastAPI 应用实例。

    gateway 为 None 时自动构建含 AgentResolver 的最小 Gateway。
    测试时可注入完整 Gateway（带真实或 mock Runner）。
    """
    app = FastAPI(title="Mini Claw Web API", version="0.1.0", lifespan=_make_lifespan())

    # 初始化 SQLite
    conn = get_connection(db_path)
    init_db(conn)

    # 构建 stores
    _expert_store = expert_store or SqliteExpertStore(conn)
    _agent_store = agent_store or SqliteAgentStore(conn)

    # 首次启动时导入 bundled 专家（SQLite 无数据才触发，避免重复）
    if not _expert_store.list_all():
        _expert_store.init_bundled()

    # 确保默认 Agent 存在
    _agent_store.ensure_default()

    # 构建 gateway（如果未注入）
    _gateway = gateway
    if _gateway is None:
        _session_store = SqliteSessionStore(conn)
        _gateway, _wrapped_runner, _memory_manager = _build_default_gateway(
            _agent_store, _session_store,
        )
        _agent_run_service = AgentRunService(
            agent_runner=_wrapped_runner,
            session_store=_session_store,
            memory_manager=_memory_manager,
        )
    else:
        # gateway 注入时，从 gateway 公共属性获取共享组件
        _session_store = _gateway.session_store
        _agent_run_service = AgentRunService(
            agent_runner=_gateway.agent_runner,
            session_store=_session_store,
            memory_manager=_gateway.memory_manager,
            compressor=_gateway.compressor,
            agent_resolver=_gateway.agent_resolver,
        )

    # 注册路由
    app.include_router(experts.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")

    # --- 统一在 _gateway 确定后创建 Web Channel 组件 ---
    from claw.channels.web.adapter import WebAdapter
    from claw.channels.web.transport import WebTransport
    from claw.processor import ChannelProcessor, ErrorPolicy, InMemoryDedupeStore

    web_transport = WebTransport()
    web_processor = ChannelProcessor(
        adapter=WebAdapter(),
        gateway=_gateway,
        dedupe_store=InMemoryDedupeStore(),
        error_policy=ErrorPolicy.RAISE,  # Web SSE 需要 Router 外层 catch 并 encode_error
    )

    # 将依赖注入到路由
    app.state.gateway = _gateway
    app.state.web_transport = web_transport
    app.state.web_processor = web_processor
    app.state.expert_store = _expert_store
    app.state.agent_store = _agent_store

    # 构建定时任务管理服务（lifespan 负责启停）
    _task_service = TaskService(
        session_store=_session_store,
        agent_run_service=_agent_run_service,
    )
    app.state.task_service = _task_service

    _wire_deps(app, _gateway, _expert_store, _agent_store)

    return app


def _make_lifespan():
    """构建 FastAPI lifespan：启动/停止 TaskService（含 Scheduler）。"""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ts: TaskService = app.state.task_service
        await ts.start()
        yield
        await ts.stop()
    return lifespan


def _build_default_gateway(
    agent_store: SqliteAgentStore, session_store: SqliteSessionStore
) -> tuple:
    """构建 Gateway 及其共享组件，供 AgentRunService 复用。

    返回 (gateway, wrapped_runner, memory_manager)。
    wrapped_runner = ContextBuildingAgentRunner 实例，同时传给 Gateway 和 AgentRunService。
    """
    from claw.agent_runtime.context import RuntimeContextBuilder
    from claw.agent_runtime.wrapper import ContextBuildingAgentRunner
    from claw.builtin_tools import register_all
    from claw.deepseek import DeepSeekAgentRunner
    from claw.memory import MemoryManager
    from claw.skills.registry import SkillsRegistry
    from claw.tools import ToolsRegistry

    # 注册内置工具（read_file, write_file, run_command 等）
    tools_registry = ToolsRegistry()
    skills_registry = SkillsRegistry()
    register_all(tools_registry, skills_registry=skills_registry)

    # DeepSeekAgentRunner：通过 OpenAI 兼容接口调用 LLM
    runner = DeepSeekAgentRunner(tools_registry=tools_registry)

    # Memory Manager：context_builder 和 Gateway 共享同一实例
    memory_manager = MemoryManager()

    # 包装 runner：上下文注入对所有 Runner 生效
    context_builder = RuntimeContextBuilder(
        memory_manager=memory_manager,
        skills_registry=skills_registry,
    )
    wrapped_runner = ContextBuildingAgentRunner(runner, context_builder)

    # AgentResolver：按 session.agent_id 解析运行配置
    resolver = AgentResolver(agent_store)

    gateway = RuntimeGateway(
        session_store=session_store,
        agent_runner=wrapped_runner,
        delivery=_NoopDelivery(),
        memory_manager=memory_manager,
        agent_resolver=resolver,
    )

    return gateway, wrapped_runner, memory_manager


class _NoopDelivery:
    """Web 端不需要 Delivery 投递（SSE 直接返回），提供空实现。"""

    async def send(self, message, reply):
        pass


def _wire_deps(
    app: FastAPI,
    gateway: RuntimeGateway,
    expert_store: SqliteExpertStore,
    agent_store: SqliteAgentStore,
) -> None:
    """将 gateway/store 注入到路由的 Depends 中。"""
    from web.backend.deps import (
        get_agent_factory,
        get_agent_resolver,
        get_agent_store,
        get_expert_marketplace,
        get_expert_registry,
        get_expert_service,
        get_expert_store,
    )

    # FastAPI dependency_overrides 机制：
    # 路由通过 Depends(get_expert_store) 声明依赖，这里用工厂创建的真实实例替换 deps.py 中的默认实现。
    # 好处：测试时可注入 mock，生产时可注入共享实例（同一个 SQLite 连接、同一个 Gateway）。
    app.dependency_overrides[get_expert_store] = lambda: expert_store
    app.dependency_overrides[get_expert_service] = lambda: ExpertService(expert_store)
    app.dependency_overrides[get_expert_registry] = lambda: ExpertRegistry(expert_store)
    app.dependency_overrides[get_expert_marketplace] = lambda: ExpertMarketplace(expert_store)
    app.dependency_overrides[get_agent_store] = lambda: agent_store
    app.dependency_overrides[get_agent_factory] = lambda: AgentFactory(expert_store, agent_store)
    app.dependency_overrides[get_agent_resolver] = lambda: AgentResolver(agent_store)

    # conversations/chat 路由依赖 Gateway（含 session 管理、agent runner、delivery），
    # 必须注入完整 Gateway 而非从 deps.py 默认构建（默认是 NoopRunner）
    app.dependency_overrides[conversations.get_gateway] = lambda: gateway
    app.dependency_overrides[chat.get_gateway] = lambda: gateway

    # tasks 路由从 app.state 获取 TaskService，无需额外 override
