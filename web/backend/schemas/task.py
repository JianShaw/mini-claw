"""定时任务相关 Pydantic Schema。"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# 任务名：字母、数字、下划线、连字符，最长 64 字符
_TASK_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class TriggerSchema(BaseModel):
    """触发器配置。"""

    type: str  # "cron" | "interval" | "event"
    expression: str | None = None  # cron 表达式，type="cron" 时必填
    seconds: int | None = None  # 间隔秒数，type="interval" 时必填
    event_name: str | None = None  # 事件名，type="event" 时有值
    idle_timeout_seconds: float | None = None  # idle 超时秒数，event 类型

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("cron", "interval", "event"):
            raise ValueError(f"trigger.type must be 'cron', 'interval', or 'event', got '{v}'")
        return v


class TaskRunRecordSchema(BaseModel):
    """单次任务执行记录。"""

    task_name: str
    triggered_at: str
    completed_at: str
    success: bool
    task_type: str  # "llm" | "system"
    message: str = ""
    error: str | None = None


class TaskSchema(BaseModel):
    """任务列表项。"""

    name: str
    description: str = ""
    trigger: TriggerSchema
    task_type: str  # "llm" | "system"
    enabled: bool = True
    peer_key: str | None = None
    prompt: str | None = None
    agent_id: str | None = None  # 关联的 agent
    session_id: str | None = None  # 关联的推送会话
    is_running: bool = False
    last_success: bool | None = None
    last_message: str = ""
    last_error: str | None = None


class TaskDetailSchema(TaskSchema):
    """任务详情，含执行历史。"""

    history: list[TaskRunRecordSchema] = []


class CreateTaskRequest(BaseModel):
    """创建 LLM 调度任务请求。选择 agent + prompt。"""

    name: str = Field(max_length=64)
    description: str = Field(default="", max_length=500)
    trigger: TriggerSchema
    agent_id: str = Field(max_length=64)
    prompt: str = Field(max_length=2000)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _TASK_NAME_RE.match(v):
            raise ValueError("name must be 1-64 chars, only letters, digits, underscore, hyphen")
        return v

    @field_validator("trigger")
    @classmethod
    def validate_trigger(cls, v: TriggerSchema) -> TriggerSchema:
        if v.type == "cron" and not v.expression:
            raise ValueError("cron trigger requires 'expression'")
        if v.type == "interval" and (v.seconds is None or v.seconds <= 0):
            raise ValueError("interval trigger requires positive 'seconds'")
        return v


class UpdateTaskRequest(BaseModel):
    """更新 LLM 调度任务请求。所有字段可选。"""

    description: str | None = Field(default=None, max_length=500)
    trigger: TriggerSchema | None = None
    prompt: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None


class ToggleRequest(BaseModel):
    """启停切换请求。"""

    enabled: bool


class TriggerResultSchema(BaseModel):
    """手动触发结果。"""

    success: bool
    message: str = ""
    error: str | None = None
