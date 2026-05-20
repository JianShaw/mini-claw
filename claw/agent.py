"""MiniClaw 门面：对外保持简单的 reply(text) 接口，对内组合所有运行时模块。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Any

from claw.channels.local import LocalAdapter, LocalTransport
from claw.gateway import RuntimeGateway
from claw.processor import ChannelProcessor, InMemoryDedupeStore
from claw.deepseek import DeepSeekAgentRunner
from claw.ports import AgentRunner, SessionStore
from claw.session import JsonlSessionStore
from claw.ports import Delivery
from claw.tools import ToolsRegistry
from claw.types import AgentReply, InboundMessage, PlatformEvent, Session, StreamChunk
from claw.agent_runtime.context import RuntimeContextBuilder
from claw.agent_runtime.wrapper import ContextBuildingAgentRunner

if True:  # 避免循环导入
    from claw.skills.registry import SkillsRegistry
    from claw.skills.types import Skill

logger = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    """从环境变量读取整数，无效值时返回默认值。"""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


class MiniClaw:
    """组合根：在这里组装所有依赖关系，外部只需调用 reply()。

    数据流：text → Transport.receive() → PlatformEvent
      → Processor(去重/适配/校验/过滤)
        → Gateway(会话/Agent/投递)
          → Delivery.send()
    """

    def __init__(
        self,
        delivery: Delivery | None = None,
        *,
        agent_runner: AgentRunner | None = None,
        api_key: str | None = None,
        session_store: SessionStore | None = None,
        auto_compact: bool = True,
        max_tokens: int | None = None,
        keep_rounds: int | None = None,
        tools_registry: ToolsRegistry | None = None,
        mcp_config_path: str | None = None,
        memory_manager: Any | None = None,
        schedule_config_path: str | None = None,
        skills_registry: SkillsRegistry | None = None,
    ) -> None:
        self.transport = LocalTransport()
        self.delivery = delivery or _default_delivery()
        runner = agent_runner or DeepSeekAgentRunner(api_key=api_key, tools_registry=tools_registry)
        self._session_store = session_store or JsonlSessionStore()

        # 环境变量优先，参数次之，最后用默认值
        resolved_max_tokens = _env_int("COMPACT_MAX_TOKENS", max_tokens or 8000)
        resolved_keep_rounds = _env_int("COMPACT_KEEP_ROUNDS", keep_rounds or 4)

        # 自动压缩：必须用原始 runner 创建 compressor
        # （isinstance 检查 wrapper 会失败，导致自动压缩静默失效）
        compressor = None
        if auto_compact and isinstance(runner, DeepSeekAgentRunner):
            from claw.compressor import ContextCompressor
            compressor = ContextCompressor(
                client=runner.client,
                model=runner.model,
                max_tokens=resolved_max_tokens,
                keep_rounds=resolved_keep_rounds,
            )

        # 包装 runner：上下文注入对所有 Runner 生效
        context_builder = RuntimeContextBuilder(
            memory_manager=memory_manager,
            skills_registry=skills_registry,
        )
        wrapped_runner = ContextBuildingAgentRunner(runner, context_builder)

        self.gateway = RuntimeGateway(
            session_store=self._session_store,
            agent_runner=wrapped_runner,
            delivery=self.delivery,
            compressor=compressor,
            memory_manager=memory_manager,
        )
        self.processor = ChannelProcessor(
            adapter=LocalAdapter(),
            gateway=self.gateway,
            dedupe_store=InMemoryDedupeStore(),
        )

        # MCP 集成：可选的外部工具源
        self._mcp_config_path = mcp_config_path
        self._mcp_manager: Any = None  # McpManager，延迟初始化
        self._tools_registry = tools_registry
        self._memory_manager = memory_manager
        self._schedule_config_path = schedule_config_path
        self._skills_registry = skills_registry

        # CLI peer 身份（构造时确定，运行期间不变）
        _id_msg = self._routing_message()
        self._peer_key = f"{_id_msg.channel}:{_id_msg.account_id}:{_id_msg.peer_id}"
        self._peer_channel = _id_msg.channel
        self._peer_account_id = _id_msg.account_id
        self._peer_peer_id = _id_msg.peer_id
        self._peer_sender_id = _id_msg.sender_id
        self._scheduler: Any = None  # TaskScheduler，延迟初始化
        if self._tools_registry is not None and self._schedule_config_path:
            from claw.builtin_tools.scheduler import register as register_scheduler_tool
            register_scheduler_tool(
                self._tools_registry,
                self.create_scheduled_task,
            )

    def _routing_message(self, text: str = "") -> InboundMessage:
        """构造一条 InboundMessage 用于获取路由字段（channel/account_id/peer_id）。"""
        event: PlatformEvent = self.transport.receive(text or "_")
        return LocalAdapter().to_inbound_message(event)

    def reply(self, text: str) -> AgentReply:
        """同步接口，供 CLI 使用。"""
        return asyncio.run(self.areply(text))

    async def areply(self, text: str) -> AgentReply:
        """异步接口：通过 Transport 将文本转为 PlatformEvent，走完整处理链路。"""
        event: PlatformEvent = self.transport.receive(text)
        reply: AgentReply | None = await self.processor.process(event)
        return reply if reply is not None else AgentReply(text="")

    async def areply_stream(self, text: str) -> AsyncIterator[StreamChunk]:
        """流式异步接口：通过流式链路逐步返回 StreamChunk。"""
        event: PlatformEvent = self.transport.receive(text)
        async for chunk in self.processor.process_stream(event):
            yield chunk

    # --- 会话管理便捷方法 ---

    async def new_session(self) -> Session:
        """创建新会话并激活。"""
        return await self.gateway.create_new_session(
            self._peer_key,
            channel=self._peer_channel,
            account_id=self._peer_account_id,
            peer_id=self._peer_peer_id,
            sender_id=self._peer_sender_id,
        )

    async def list_sessions(self) -> list[Session]:
        """列出当前 peer 下的所有会话。"""
        return await self.gateway.list_sessions(self._peer_key)

    async def select_session(self, session_id: str) -> Session | None:
        """切换到指定会话。"""
        return await self.gateway.select_session(self._peer_key, session_id)

    async def delete_session(self, session_id: str) -> None:
        """删除指定会话。"""
        return await self.gateway.delete_session(self._peer_key, session_id)

    async def compact_session(self) -> str | None:
        """压缩当前会话的上下文，返回生成的摘要。"""
        return await self.gateway.compact_session(self._peer_key)

    async def memory_today(self) -> str:
        """读取当天 daily memory，用于 CLI 的 /memory today。"""
        if self._memory_manager is None:
            return ""
        return self._memory_manager.daily_store.read(self._memory_manager.today())

    async def memory_long(self) -> str:
        """读取长期记忆，用于 CLI 的 /memory long。"""
        if self._memory_manager is None:
            return ""
        return self._memory_manager.long_store.read()

    async def update_memory_today(self) -> bool:
        """手动强制更新当天 daily memory。"""
        if self._memory_manager is None:
            return False
        session = await self._session_store.get_active(self._peer_key)
        if session is None:
            return False
        return await self._memory_manager.force_update_daily(session)

    async def distill_memory(self) -> int:
        """手动把 daily memory 的长期候选提炼进 MEMORY.md。"""
        if self._memory_manager is None:
            return 0
        result = await self._memory_manager.distill_daily_to_long_term()
        return result.added

    async def get_active_session_id(self) -> str | None:
        """获取当前活跃会话的 ID。"""
        session = await self._session_store.get_active(self._peer_key)
        return session.session_id if session else None

    # --- MCP 生命周期管理 ---

    async def start(self) -> None:
        """启动 MCP 连接、定时任务调度器和技能加载。无配置时为空操作。"""
        # 技能加载
        if self._skills_registry is not None and hasattr(self._skills_registry, 'load_from_store'):
            from claw.skills.store import SkillStore
            store = SkillStore()
            self._skills_registry.set_store(store)
            await self._skills_registry.load_from_store()

        # MCP 启动
        if self._mcp_config_path:
            try:
                from claw.mcp.manager import McpManager
                self._mcp_manager = McpManager.from_config_file(self._mcp_config_path)
                await self._mcp_manager.start()
                if self._tools_registry is not None:
                    self._mcp_manager.register_tools(self._tools_registry)
            except Exception as exc:
                if self._mcp_manager is not None:
                    try:
                        await self._mcp_manager.stop()
                    except Exception:
                        pass
                    self._mcp_manager = None
                logger.error("Failed to start MCP manager: %s", exc)

        # 定时任务调度器启动
        if self._schedule_config_path:
            try:
                from claw.scheduler import TaskScheduler
                from claw.scheduler.config import ScheduleConfigLoader
                from claw.scheduler.context import TaskContext
                from claw.scheduler.history import TaskRunHistory

                context = TaskContext(
                    _session_store=self._session_store,
                    _memory_manager=self._memory_manager,
                    _gateway=self.gateway,
                )
                history = TaskRunHistory()
                self._scheduler = TaskScheduler(
                    gateway=self.gateway,
                    context=context,
                    history=history,
                )
                definitions = ScheduleConfigLoader.load(self._schedule_config_path)
                for defn in definitions:
                    self._scheduler.register(defn)
                await self._scheduler.start()
            except Exception as exc:
                if self._scheduler is not None:
                    try:
                        await self._scheduler.stop()
                    except Exception:
                        pass
                    self._scheduler = None
                logger.error("Failed to start scheduler: %s", exc)

    async def stop(self) -> None:
        """停止 MCP 连接和定时任务调度器。"""
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        if self._mcp_manager is not None:
            await self._mcp_manager.stop()
            self._mcp_manager = None

    def get_mcp_status(self) -> list[Any]:
        """返回 MCP 服务器状态列表。"""
        if self._mcp_manager is None:
            return []
        return self._mcp_manager.get_status()

    # --- 调度器便捷方法 ---

    def get_task_status(self) -> list[dict[str, Any]]:
        """返回所有定时任务状态列表。"""
        if self._scheduler is None:
            return []
        return self._scheduler.list_tasks()

    async def run_task(self, name: str) -> Any:
        """手动触发指定定时任务。"""
        if self._scheduler is None:
            return None
        return await self._scheduler.run_now(name)

    async def create_scheduled_task(self, args: dict[str, Any]) -> str:
        """Create a scheduled LLM task from tool-call arguments."""
        if not self._schedule_config_path:
            return "Error: scheduler is not configured."

        from claw.scheduler.config import (
            ScheduleConfigLoader,
            upsert_task_config,
        )
        from claw.scheduler.types import TaskDefinition

        name = self._normalize_task_name(str(args.get("name") or "scheduled_task"))
        trigger_data = args.get("trigger")
        if not isinstance(trigger_data, dict):
            return "Error: trigger must be an object."
        trigger = ScheduleConfigLoader._parse_trigger(trigger_data)
        if trigger is None:
            return "Error: invalid trigger."
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return "Error: prompt is required."
        if name in {"daily_distill", "periodic_memory", "idle_compact"}:
            return f"Error: task name '{name}' is reserved."

        replace = bool(args.get("replace", False))
        existing_names = {s["name"] for s in self.get_task_status()}
        if self._scheduler is not None and name in existing_names and not replace:
            return f"Error: task '{name}' already exists. Set replace=true to update it."

        definition = TaskDefinition(
            name=name,
            trigger=trigger,
            handler=None,
            enabled=bool(args.get("enabled", True)),
            description=str(args.get("description") or prompt[:80]),
            params={},
            peer_key=self._current_peer_key(),
            prompt=prompt,
        )
        upsert_task_config(self._schedule_config_path, definition)
        if self._scheduler is not None:
            await self._scheduler.upsert(definition)
        return f"Scheduled task '{name}' created."

    async def emit_event(self, event_name: str, **payload: Any) -> None:
        """发射命名事件到调度器（如 session_activity）。"""
        if self._scheduler is not None:
            await self._scheduler.emit(event_name, **payload)

    def _current_peer_key(self) -> str:
        """获取当前 peer 的 peer_key。"""
        return self._peer_key

    def _normalize_task_name(self, name: str) -> str:
        """Normalize an LLM-provided task name for config keys."""
        value = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower())
        value = re.sub(r"_+", "_", value).strip("_-")
        return value[:64] or "scheduled_task"

    # --- 技能管理便捷方法 ---

    async def activate_skill(self, name: str) -> Skill:
        """激活指定技能。"""
        if self._skills_registry is None:
            raise RuntimeError("Skills registry not configured")
        return self._skills_registry.activate(name)

    async def deactivate_skill(self) -> None:
        """停用当前激活的技能。"""
        if self._skills_registry is not None:
            self._skills_registry.deactivate()

    async def list_skills(self) -> list[Skill]:
        """列出所有已注册技能。"""
        if self._skills_registry is None:
            return []
        return self._skills_registry.list()


def _default_delivery() -> Delivery:
    """默认使用 LocalDelivery，会话持久化由 JsonlSessionStore 接管。"""
    from claw.channels.local import LocalDelivery

    return LocalDelivery()
