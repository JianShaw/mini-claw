"""ScheduleConfigLoader：从 JSON 加载任务配置，含 cron 语法校验。"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from claw.scheduler.types import (
    CronTrigger,
    EventTrigger,
    IntervalTrigger,
    TaskDefinition,
    Trigger,
)

logger = logging.getLogger(__name__)

# Cron 字段范围约束
_CRON_RANGES: list[tuple[int, int]] = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week
]

# 合法的 cron 字段值：数字、*/N、逗号分隔
_CRON_FIELD_RE = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")


def validate_cron(expression: str) -> None:
    """校验 cron 表达式，不合法时抛出 ValueError。

    支持语法：*、*/N、数字、逗号分隔列表。不支持 L/W/# 等扩展语法。
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Cron 表达式必须是 5 个字段: {expression!r}")
    for i, part in enumerate(parts):
        lo, hi = _CRON_RANGES[i]
        if part == "*":
            continue
        if part.startswith("*/"):
            step_str = part[2:]
            if not step_str.isdigit():
                raise ValueError(f"Cron 字段 {i} 步进值无效: {part!r}")
            step = int(step_str)
            if step <= 0 or step > hi:
                raise ValueError(f"Cron 字段 {i} 步进值越界: {part!r}")
            continue
        # 逗号分隔的数字或数字范围
        if not _CRON_FIELD_RE.match(part):
            raise ValueError(f"Cron 字段 {i} 语法不支持: {part!r}")
        for item in part.split(","):
            if "-" in item:
                a, b = item.split("-", 1)
                if not (lo <= int(a) <= hi and lo <= int(b) <= hi):
                    raise ValueError(f"Cron 字段 {i} 值越界: {item!r}")
            else:
                val = int(item)
                if not (lo <= val <= hi):
                    raise ValueError(f"Cron 字段 {i} 值越界: {val}")


class ScheduleConfigLoader:
    """从 JSON 配置文件加载任务定义。"""

    @staticmethod
    def load(path: str | Path) -> list[TaskDefinition]:
        """加载配置文件，文件不存在或格式错误时返回空列表。"""
        config_path = Path(path)
        if not config_path.exists():
            logger.debug("Schedule config not found: %s", path)
            return []
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load schedule config %s: %s", path, exc)
            return []
        return ScheduleConfigLoader._parse(data)

    @staticmethod
    def _parse(data: dict[str, Any]) -> list[TaskDefinition]:
        tasks_data = data.get("tasks")
        if not isinstance(tasks_data, dict):
            return []
        definitions: list[TaskDefinition] = []
        for name, task_data in tasks_data.items():
            if not isinstance(task_data, dict):
                continue
            trigger = ScheduleConfigLoader._parse_trigger(task_data.get("trigger", {}))
            if trigger is None:
                logger.warning("Skipping task '%s': invalid trigger", name)
                continue
            definitions.append(TaskDefinition(
                name=name,
                trigger=trigger,
                handler=task_data.get("handler"),
                enabled=task_data.get("enabled", True),
                description=task_data.get("description", ""),
                params=task_data.get("params", {}),
                peer_key=task_data.get("peer_key"),
                prompt=task_data.get("prompt"),
            ))
        return definitions

    @staticmethod
    def _parse_trigger(data: dict[str, Any]) -> Trigger | None:
        trigger_type = data.get("type")
        if trigger_type == "cron":
            expr = data.get("expression", "")
            if not expr:
                return None
            try:
                validate_cron(expr)
            except ValueError as exc:
                logger.warning("Invalid cron expression '%s': %s", expr, exc)
                return None
            return CronTrigger(expression=expr)
        elif trigger_type == "interval":
            seconds = data.get("seconds", 0)
            if not isinstance(seconds, (int, float)) or seconds <= 0:
                return None
            return IntervalTrigger(seconds=int(seconds))
        elif trigger_type == "event":
            event_name = data.get("event_name", "")
            if not event_name:
                return None
            return EventTrigger(
                event_name=event_name,
                idle_timeout_seconds=data.get("idle_timeout_seconds"),
            )
        return None


def trigger_to_config(trigger: Trigger) -> dict[str, Any]:
    """Convert a trigger dataclass back to JSON config."""
    if isinstance(trigger, CronTrigger):
        return {"type": "cron", "expression": trigger.expression}
    if isinstance(trigger, IntervalTrigger):
        return {"type": "interval", "seconds": trigger.seconds}
    if isinstance(trigger, EventTrigger):
        data: dict[str, Any] = {"type": "event", "event_name": trigger.event_name}
        if trigger.idle_timeout_seconds is not None:
            data["idle_timeout_seconds"] = trigger.idle_timeout_seconds
        return data
    raise TypeError(f"Unsupported trigger: {trigger!r}")


def task_to_config(definition: TaskDefinition) -> dict[str, Any]:
    """Convert a TaskDefinition to the schedule_config.json shape."""
    result: dict[str, Any] = {
        "trigger": trigger_to_config(definition.trigger),
        "handler": definition.handler,
        "enabled": definition.enabled,
        "description": definition.description,
        "params": definition.params,
    }
    if definition.peer_key is not None:
        result["peer_key"] = definition.peer_key
    if definition.prompt is not None:
        result["prompt"] = definition.prompt
    return result


def upsert_task_config(path: str | Path, definition: TaskDefinition) -> None:
    """Persist a task definition into schedule_config.json."""
    config_path = Path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    tasks = data.setdefault("tasks", {})
    if not isinstance(tasks, dict):
        tasks = {}
        data["tasks"] = tasks
    tasks[definition.name] = task_to_config(definition)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
