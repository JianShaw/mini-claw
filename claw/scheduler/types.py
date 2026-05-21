"""定时任务调度系统的类型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class CronTrigger:
    """Cron 式触发器：在指定时间执行。

    支持 5 字段表达式：minute hour day-of-month month day-of-week
    示例：``"0 9 * * *"``（每天 9 点），``"*/30 * * * *"``（每 30 分钟）
    """

    expression: str


@dataclass(slots=True)
class IntervalTrigger:
    """间隔触发器：每隔 N 秒执行一次。"""

    seconds: int


@dataclass(slots=True)
class EventTrigger:
    """事件驱动触发器：当指定事件被 emit 时重置计时器，超时无事件则触发。

    ``idle_timeout_seconds`` 为 ``None`` 时仅响应 emit，不自动触发。
    """

    event_name: str
    idle_timeout_seconds: float | None = None


Trigger = CronTrigger | IntervalTrigger | EventTrigger


@dataclass(slots=True)
class TaskDefinition:
    """声明式任务定义，可从配置文件加载或编程式注册。

    两种模式：
    - LLM 任务：设置 peer_key + prompt，触发时生成 InboundMessage 走 gateway 全链路
    - 系统任务：设置 handler（callable 或 dotted path），触发时直接调用
    """

    name: str
    trigger: Trigger
    handler: Any = None  # Callable 或 dotted path 字符串（系统任务用）
    enabled: bool = True
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    # LLM 任务字段：设置后触发时走 gateway → session → agent → delivery
    peer_key: str | None = None
    prompt: str | None = None

    @property
    def is_llm_task(self) -> bool:
        """是否为 LLM 任务（通过 gateway 路由）。"""
        return self.peer_key is not None and self.prompt is not None


@dataclass(slots=True)
class AgentRun:
    """调度器内部执行请求：明确"对哪个 session、用哪个 agent、跑什么 prompt"。

    与 InboundMessage（外部通道消息格式）完全解耦。
    AgentRunService 负责将 AgentRun 转化为 AgentRunner 需要的 (session, message)。
    """

    session_id: str
    agent_id: str
    peer_key: str
    prompt: str
    task_name: str


@dataclass(slots=True)
class TaskResult:
    """单次任务执行结果。"""

    task_name: str
    success: bool
    message: str = ""
    error: str | None = None


@dataclass(slots=True)
class TaskRunRecord:
    """单次任务执行记录，持久化到 history JSONL。"""

    task_name: str
    triggered_at: str  # ISO 8601
    completed_at: str  # ISO 8601
    success: bool
    task_type: str  # "llm" or "system"
    message: str = ""
    error: str | None = None
