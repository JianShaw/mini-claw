"""配置加载测试：正常加载、缺失文件、非法 JSON、cron 校验。"""
from __future__ import annotations

import json
from pathlib import Path

from claw.scheduler.config import ScheduleConfigLoader, upsert_task_config, validate_cron
from claw.scheduler.types import CronTrigger, EventTrigger, IntervalTrigger
from claw.scheduler.types import TaskDefinition


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# --- 正常加载 ---


async def test_load_valid_config(tmp_path: Path) -> None:
    config = tmp_path / "schedule.json"
    _write_json(config, {
        "tasks": {
            "daily": {
                "trigger": {"type": "cron", "expression": "0 3 * * *"},
                "handler": "claw.scheduler.tasks.daily_memory_distill",
                "enabled": True,
                "description": "daily distill",
            },
            "periodic": {
                "trigger": {"type": "interval", "seconds": 1800},
                "handler": "claw.scheduler.tasks.periodic_memory_update",
            },
            "idle": {
                "trigger": {"type": "event", "event_name": "session_activity", "idle_timeout_seconds": 600},
                "handler": "claw.scheduler.tasks.idle_auto_compact",
                "enabled": False,
            },
        },
    })
    defs = ScheduleConfigLoader.load(config)
    assert len(defs) == 3
    assert isinstance(defs[0].trigger, CronTrigger)
    assert isinstance(defs[1].trigger, IntervalTrigger)
    assert isinstance(defs[2].trigger, EventTrigger)
    assert defs[2].enabled is False


# --- 异常处理 ---


async def test_load_missing_file(tmp_path: Path) -> None:
    defs = ScheduleConfigLoader.load(tmp_path / "nonexistent.json")
    assert defs == []


async def test_load_invalid_json(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text("{invalid", encoding="utf-8")
    defs = ScheduleConfigLoader.load(config)
    assert defs == []


async def test_load_empty_tasks(tmp_path: Path) -> None:
    config = tmp_path / "empty.json"
    _write_json(config, {"tasks": {}})
    defs = ScheduleConfigLoader.load(config)
    assert defs == []


async def test_upsert_task_config_persists_dynamic_task(tmp_path: Path) -> None:
    config = tmp_path / "schedule.json"
    upsert_task_config(config, TaskDefinition(
        name="remind_me",
        trigger=IntervalTrigger(seconds=60),
        handler=None,
        description="reminder",
        peer_key="local:app:user",
        prompt="say hi",
    ))
    defs = ScheduleConfigLoader.load(config)
    assert len(defs) == 1
    assert defs[0].name == "remind_me"
    assert defs[0].peer_key == "local:app:user"
    assert defs[0].prompt == "say hi"
    assert defs[0].is_llm_task is True


# --- trigger 解析 ---


async def test_parse_cron_trigger(tmp_path: Path) -> None:
    config = tmp_path / "cron.json"
    _write_json(config, {"tasks": {"t": {
        "trigger": {"type": "cron", "expression": "*/15 * * * *"},
        "handler": "x",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert len(defs) == 1
    assert defs[0].trigger.expression == "*/15 * * * *"


async def test_parse_interval_trigger(tmp_path: Path) -> None:
    config = tmp_path / "interval.json"
    _write_json(config, {"tasks": {"t": {
        "trigger": {"type": "interval", "seconds": 60},
        "handler": "x",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert isinstance(defs[0].trigger, IntervalTrigger)
    assert defs[0].trigger.seconds == 60


async def test_parse_event_trigger_with_idle(tmp_path: Path) -> None:
    config = tmp_path / "event.json"
    _write_json(config, {"tasks": {"t": {
        "trigger": {"type": "event", "event_name": "activity", "idle_timeout_seconds": 300},
        "handler": "x",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert isinstance(defs[0].trigger, EventTrigger)
    assert defs[0].trigger.idle_timeout_seconds == 300


async def test_parse_event_trigger_without_idle(tmp_path: Path) -> None:
    config = tmp_path / "event2.json"
    _write_json(config, {"tasks": {"t": {
        "trigger": {"type": "event", "event_name": "ping"},
        "handler": "x",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert isinstance(defs[0].trigger, EventTrigger)
    assert defs[0].trigger.idle_timeout_seconds is None


async def test_invalid_trigger_type_skipped(tmp_path: Path) -> None:
    config = tmp_path / "bad_trigger.json"
    _write_json(config, {"tasks": {"t": {
        "trigger": {"type": "unknown"},
        "handler": "x",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert defs == []


async def test_invalid_cron_skipped(tmp_path: Path) -> None:
    config = tmp_path / "bad_cron.json"
    _write_json(config, {"tasks": {"t": {
        "trigger": {"type": "cron", "expression": "L * * * *"},
        "handler": "x",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert defs == []


async def test_zero_interval_skipped(tmp_path: Path) -> None:
    config = tmp_path / "zero.json"
    _write_json(config, {"tasks": {"t": {
        "trigger": {"type": "interval", "seconds": 0},
        "handler": "x",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert defs == []


# --- cron 校验 ---


def test_validate_cron_valid_expressions() -> None:
    validate_cron("0 9 * * *")
    validate_cron("*/15 * * * *")
    validate_cron("0,30 * * * *")
    validate_cron("0 0 1 1 *")


def test_validate_cron_rejects_wrong_field_count() -> None:
    try:
        validate_cron("0 9 * *")
        assert False
    except ValueError:
        pass


def test_validate_cron_rejects_l_syntax() -> None:
    try:
        validate_cron("L * * * *")
        assert False
    except ValueError:
        pass


def test_validate_cron_rejects_out_of_range() -> None:
    try:
        validate_cron("60 9 * * *")
        assert False
    except ValueError:
        pass


def test_validate_cron_rejects_invalid_step() -> None:
    try:
        validate_cron("*/0 * * * *")
        assert False
    except ValueError:
        pass


# --- peer_key / prompt 字段 ---


async def test_load_config_with_peer_key_and_prompt(tmp_path: Path) -> None:
    config = tmp_path / "schedule.json"
    _write_json(config, {"tasks": {"remind": {
        "trigger": {"type": "interval", "seconds": 60},
        "peer_key": "local:app:user",
        "prompt": "提醒喝水",
        "description": "喝水提醒",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert len(defs) == 1
    assert defs[0].peer_key == "local:app:user"
    assert defs[0].prompt == "提醒喝水"
    assert defs[0].is_llm_task is True
    assert defs[0].handler is None


async def test_load_config_system_task_no_peer_key(tmp_path: Path) -> None:
    config = tmp_path / "schedule.json"
    _write_json(config, {"tasks": {"distill": {
        "trigger": {"type": "cron", "expression": "0 3 * * *"},
        "handler": "claw.scheduler.tasks.daily_memory_distill",
    }}})
    defs = ScheduleConfigLoader.load(config)
    assert len(defs) == 1
    assert defs[0].peer_key is None
    assert defs[0].prompt is None
    assert defs[0].is_llm_task is False


async def test_task_to_config_includes_peer_key_for_llm_tasks() -> None:
    from claw.scheduler.config import task_to_config
    defn = TaskDefinition(
        name="remind",
        trigger=IntervalTrigger(seconds=60),
        peer_key="local:app:user",
        prompt="提醒",
    )
    data = task_to_config(defn)
    assert data["peer_key"] == "local:app:user"
    assert data["prompt"] == "提醒"


async def test_task_to_config_omits_peer_key_when_none() -> None:
    from claw.scheduler.config import task_to_config
    defn = TaskDefinition(
        name="distill",
        trigger=CronTrigger(expression="0 3 * * *"),
        handler="claw.scheduler.tasks.daily_memory_distill",
    )
    data = task_to_config(defn)
    assert "peer_key" not in data
    assert "prompt" not in data
