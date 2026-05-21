"""TaskService：Web 层与 Scheduler 之间的唯一入口。

职责边界：
  - 只管理 LLM 任务（peer_key + prompt），系统任务只读
  - 所有变更先持久化到 schedule_config.json，再更新内存中的调度器
  - 不暴露调度器内部状态（_tasks, _handlers 等）
  - 创建任务时自动创建专用 session（通过 agent_id）
"""
from __future__ import annotations

import logging
from typing import Any

from claw.channels.web.adapter import (
    WEB_ACCOUNT_ID,
    WEB_CHANNEL,
    WEB_PEER_ID,
    WEB_SENDER_ID,
)
from claw.scheduler.config import ScheduleConfigLoader, upsert_task_config
from claw.scheduler.history import TaskRunHistory
from claw.scheduler.scheduler import TaskScheduler
from claw.scheduler.types import (
    CronTrigger,
    EventTrigger,
    IntervalTrigger,
    TaskDefinition,
    TaskRunRecord,
    Trigger,
)
from claw.session import build_peer_key, create_session_from_identity

if True:  # 避免循环导入
    from claw.gateway import RuntimeGateway

logger = logging.getLogger(__name__)

# 定时推送会话的 peer_id 前缀
_SCHED_PEER_PREFIX = "sched"


class TaskService:
    """定时任务管理服务：Web 层与调度器之间的唯一入口。"""

    def __init__(
        self,
        gateway: RuntimeGateway,
        config_path: str = "schedule_config.json",
        history_path: str = "data/scheduler/history.jsonl",
    ) -> None:
        self._gateway = gateway
        self._config_path = config_path
        self._history = TaskRunHistory(path=history_path)
        self._scheduler = TaskScheduler(
            gateway=gateway,
            history=self._history,
            max_workers=1,
        )
        # 本地缓存任务定义，避免每次从文件重载
        self._definitions: dict[str, TaskDefinition] = {}
        # task_name → session_id（Web 创建的 LLM 任务专用）
        self._session_map: dict[str, str] = {}

    async def start(self) -> None:
        """加载配置 → 注册任务 → 启动调度器。在 FastAPI lifespan startup 时调用。"""
        definitions = ScheduleConfigLoader.load(self._config_path)
        for definition in definitions:
            self._definitions[definition.name] = definition
            self._scheduler.register(definition)

        # 恢复 Web 创建任务的 session 映射（从 config params 中读取）
        for name, definition in self._definitions.items():
            if definition.is_llm_task and definition.params.get("session_id"):
                self._session_map[name] = definition.params["session_id"]

        await self._scheduler.start()
        logger.info(
            "TaskService started, %d task(s) loaded from %s",
            len(definitions), self._config_path,
        )

    async def stop(self) -> None:
        """停止调度器。在 FastAPI lifespan shutdown 时调用。"""
        await self._scheduler.stop()
        self._definitions.clear()
        self._session_map.clear()
        logger.info("TaskService stopped")

    def list_tasks(self) -> list[dict[str, Any]]:
        """返回所有已注册任务的摘要视图。"""
        scheduler_tasks = self._scheduler.list_tasks()
        runtime_map: dict[str, dict[str, Any]] = {
            t["name"]: t for t in scheduler_tasks
        }

        result: list[dict[str, Any]] = []
        for name, definition in self._definitions.items():
            runtime = runtime_map.get(name, {})
            last = runtime.get("last_result")
            result.append(self._build_task_view(definition, runtime, last))
        return result

    def get_task(self, name: str) -> dict[str, Any] | None:
        """返回单个任务详情，包含最近执行历史。"""
        definition = self._definitions.get(name)
        if definition is None:
            return None

        scheduler_tasks = self._scheduler.list_tasks()
        runtime = next((t for t in scheduler_tasks if t["name"] == name), {})
        last = runtime.get("last_result")

        history = self._history.get_by_task(name, limit=20)
        view = self._build_task_view(definition, runtime, last)
        view["history"] = [
            {
                "task_name": r.task_name,
                "triggered_at": r.triggered_at,
                "completed_at": r.completed_at,
                "success": r.success,
                "task_type": r.task_type,
                "message": r.message,
                "error": r.error,
            }
            for r in history
        ]
        return view

    async def create_task(
        self,
        *,
        name: str,
        trigger: Trigger,
        agent_id: str,
        prompt: str,
        description: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        """创建 LLM 任务：创建推送 session → 持久化 → 注册到 scheduler。

        自动为任务创建一个专用会话（metadata 标记 session_type="scheduled"），
        peer_key 从 session 中提取。
        """
        if name in self._definitions:
            raise ValueError(f"Task already exists: {name}")

        # 1. 创建专用推送 session
        sched_peer_id = f"{_SCHED_PEER_PREFIX}:{name}"
        session = await self._gateway.create_session_for_agent(
            peer_key=build_peer_key(WEB_CHANNEL, WEB_ACCOUNT_ID, sched_peer_id),
            agent_id=agent_id,
            channel=WEB_CHANNEL,
            account_id=WEB_ACCOUNT_ID,
            peer_id=sched_peer_id,
            sender_id=WEB_SENDER_ID,
        )
        # 标记 session 类型
        session.metadata["session_type"] = "scheduled"
        session.metadata["task_name"] = name
        await self._gateway._session_store.save(session)

        # 2. 构建 TaskDefinition，peer_key 从 session 获取
        definition = TaskDefinition(
            name=name,
            trigger=trigger,
            prompt=prompt,
            description=description,
            enabled=enabled,
            peer_key=session.session_key,
            params={
                "session_id": session.session_id,
                "agent_id": agent_id,
            },
        )

        # 3. 持久化 + 注册
        upsert_task_config(self._config_path, definition)
        self._scheduler.register(definition)
        self._definitions[name] = definition
        self._session_map[name] = session.session_id

        logger.info("Task created: %s (agent=%s, session=%s)", name, agent_id, session.session_id)
        return self._build_task_view(definition, {}, None)

    async def update_task(self, name: str, updates: dict[str, Any]) -> dict[str, Any]:
        """更新 LLM 任务：合并字段 → 持久化 → scheduler.upsert() → 返回视图。"""
        existing = self._definitions.get(name)
        if existing is None:
            raise ValueError(f"Task not found: {name}")
        if not existing.is_llm_task:
            raise ValueError(f"System task cannot be modified via web: {name}")

        new_def = self._merge_definition(existing, updates)

        upsert_task_config(self._config_path, new_def)
        await self._scheduler.upsert(new_def)
        self._definitions[name] = new_def

        logger.info("Task updated: %s", name)
        return self._build_task_view(new_def, {}, None)

    async def toggle_task(self, name: str, enabled: bool) -> dict[str, Any]:
        """切换任务启用状态：持久化 → scheduler.upsert()。"""
        existing = self._definitions.get(name)
        if existing is None:
            raise ValueError(f"Task not found: {name}")

        new_def = TaskDefinition(
            name=existing.name,
            trigger=existing.trigger,
            handler=existing.handler,
            enabled=enabled,
            description=existing.description,
            params=existing.params,
            peer_key=existing.peer_key,
            prompt=existing.prompt,
        )

        upsert_task_config(self._config_path, new_def)
        await self._scheduler.upsert(new_def)
        self._definitions[name] = new_def

        logger.info("Task toggled: %s → enabled=%s", name, enabled)
        return self._build_task_view(new_def, {}, None)

    async def trigger_task(self, name: str) -> dict[str, Any]:
        """手动触发任务：scheduler.run_now() 等待完成返回结果。"""
        if name not in self._definitions:
            raise ValueError(f"Task not found: {name}")

        result = await self._scheduler.run_now(name)
        return {
            "success": result.success,
            "message": result.message,
            "error": result.error,
        }

    async def delete_task(self, name: str) -> None:
        """删除 LLM 任务：从 scheduler 移除 + 删除配置 + 清理 session。"""
        existing = self._definitions.get(name)
        if existing is None:
            raise ValueError(f"Task not found: {name}")
        if not existing.is_llm_task:
            raise ValueError(f"System task cannot be deleted via web: {name}")

        await self._scheduler.unregister(name)
        self._definitions.pop(name, None)
        self._remove_task_config(name)

        # 清理关联的推送 session
        session_id = self._session_map.pop(name, None)
        if session_id and existing.peer_key:
            try:
                await self._gateway.delete_session(existing.peer_key, session_id)
            except Exception:
                logger.warning("Failed to delete session %s for task %s", session_id, name)

        logger.info("Task deleted: %s", name)

    def get_history(self, task_name: str, limit: int = 20) -> list[dict[str, Any]]:
        """查询指定任务的执行历史。limit 上限 200。"""
        safe_limit = max(1, min(limit, 200))
        records = self._history.get_by_task(task_name, limit=safe_limit)
        return [
            {
                "task_name": r.task_name,
                "triggered_at": r.triggered_at,
                "completed_at": r.completed_at,
                "success": r.success,
                "task_type": r.task_type,
                "message": r.message,
                "error": r.error,
            }
            for r in records
        ]

    def get_scheduled_session_ids(self) -> list[str]:
        """返回所有 LLM 定时任务关联的 session_id。"""
        return list(self._session_map.values())

    # ---- 内部方法 ----

    def _build_task_view(
        self,
        definition: TaskDefinition,
        runtime: dict[str, Any],
        last_result: Any | None,
    ) -> dict[str, Any]:
        """构建返回给 API 层的任务视图 dict。"""
        trigger_data: dict[str, Any] = {}
        if isinstance(definition.trigger, CronTrigger):
            trigger_data = {"type": "cron", "expression": definition.trigger.expression}
        elif isinstance(definition.trigger, IntervalTrigger):
            trigger_data = {"type": "interval", "seconds": definition.trigger.seconds}
        elif isinstance(definition.trigger, EventTrigger):
            trigger_data = {
                "type": "event",
                "event_name": definition.trigger.event_name,
                "idle_timeout_seconds": definition.trigger.idle_timeout_seconds,
            }

        last_success = None
        last_message = ""
        last_error = None
        if last_result is not None:
            last_success = last_result.success
            last_message = last_result.message or ""
            last_error = last_result.error

        return {
            "name": definition.name,
            "description": definition.description,
            "trigger": trigger_data,
            "task_type": "llm" if definition.is_llm_task else "system",
            "enabled": definition.enabled,
            "peer_key": definition.peer_key,
            "prompt": definition.prompt,
            "agent_id": definition.params.get("agent_id"),
            "session_id": self._session_map.get(definition.name),
            "is_running": runtime.get("is_running", False),
            "last_success": last_success,
            "last_message": last_message,
            "last_error": last_error,
        }

    @staticmethod
    def _merge_definition(
        existing: TaskDefinition, updates: dict[str, Any]
    ) -> TaskDefinition:
        """将 updates dict 合并到现有 TaskDefinition，生成新实例。"""
        trigger = existing.trigger
        if "trigger" in updates and updates["trigger"] is not None:
            t = updates["trigger"]
            if t["type"] == "cron":
                expr = t.get("expression", "")
                if expr:
                    from claw.scheduler.config import validate_cron
                    validate_cron(expr)
                trigger = CronTrigger(expression=expr)
            elif t["type"] == "interval":
                secs = t.get("seconds", 0)
                if not isinstance(secs, (int, float)) or secs <= 0:
                    raise ValueError("interval seconds must be positive")
                trigger = IntervalTrigger(seconds=int(secs))

        return TaskDefinition(
            name=existing.name,
            trigger=trigger,
            handler=existing.handler,
            enabled=updates.get("enabled", existing.enabled),
            description=updates.get("description", existing.description),
            params=existing.params,
            peer_key=existing.peer_key,
            prompt=updates.get("prompt", existing.prompt),
        )

    def _remove_task_config(self, name: str) -> None:
        """从配置文件中移除指定任务。"""
        import json
        from pathlib import Path

        config_path = Path(self._config_path)
        if not config_path.exists():
            return
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        tasks = data.get("tasks")
        if not isinstance(tasks, dict):
            return
        tasks.pop(name, None)
        config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
