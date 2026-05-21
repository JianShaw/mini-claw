"""TaskService 单元测试：验证 Service 层与 Scheduler 之间的边界。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from claw.channels.web.adapter import WEB_ACCOUNT_ID, WEB_CHANNEL, WEB_SENDER_ID
from claw.session import build_peer_key
from claw.scheduler.agent_run import AgentRunService
from claw.scheduler.types import (
    CronTrigger,
    EventTrigger,
    IntervalTrigger,
    TaskDefinition,
    TaskResult,
)
from claw.types import AgentReply, Session
from web.backend.services.task_service import TaskService


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    p = tmp_path / "schedule_config.json"
    p.write_text('{"tasks": {}}', encoding="utf-8")
    return p


@pytest.fixture
def history_path(tmp_path: Path) -> Path:
    return tmp_path / "history.jsonl"


@pytest.fixture
def mock_session_store() -> AsyncMock:
    """Mock SessionStore：record 保存的 session 供后续断言。"""
    store = AsyncMock()
    store.save = AsyncMock()
    store.set_active = AsyncMock()
    store.delete = AsyncMock()
    return store


@pytest.fixture
def mock_agent_run_service() -> AsyncMock:
    svc = AsyncMock(spec=AgentRunService)
    svc.execute = AsyncMock(return_value=AgentReply(text="定时推送完成"))
    return svc


@pytest.fixture
def task_service(
    mock_session_store: AsyncMock,
    mock_agent_run_service: AsyncMock,
    config_path: Path,
    history_path: Path,
) -> TaskService:
    return TaskService(
        session_store=mock_session_store,
        agent_run_service=mock_agent_run_service,
        config_path=str(config_path),
        history_path=str(history_path),
    )


# ---- start / stop ----


async def test_start_loads_and_registers(
    task_service: TaskService, config_path: Path
) -> None:
    config = {
        "tasks": {
            "remind": {
                "trigger": {"type": "interval", "seconds": 3600},
                "peer_key": "local:app:user",
                "prompt": "Time to drink water",
                "enabled": True,
                "description": "喝水提醒",
            }
        }
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    await task_service.start()
    tasks = task_service.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "remind"
    await task_service.stop()


async def test_stop_clears_definitions(task_service: TaskService) -> None:
    await task_service.start()
    assert task_service.list_tasks() == []
    await task_service.stop()


# ---- create_task (新流程：agent_id → 自动创建 session) ----


async def test_create_task_creates_session(
    task_service: TaskService, mock_session_store: AsyncMock, config_path: Path
) -> None:
    """创建任务时直接通过 SessionStore 创建 session。"""
    await task_service.start()

    view = await task_service.create_task(
        name="morning_greet",
        trigger=CronTrigger(expression="0 9 * * *"),
        agent_id="ag_test",
        prompt="Good morning!",
        description="早安问候",
    )

    assert view["name"] == "morning_greet"
    assert view["task_type"] == "llm"
    assert view["agent_id"] == "ag_test"
    assert view["session_id"] is not None
    assert view["peer_key"] is not None

    # 验证 session_store.save 和 set_active 被调用
    mock_session_store.save.assert_called_once()
    mock_session_store.set_active.assert_called_once()

    # 验证 peer_key 包含 sched 前缀
    set_active_call = mock_session_store.set_active.call_args
    assert set_active_call[0][0] == view["peer_key"]

    await task_service.stop()


async def test_create_task_marks_session_type(
    task_service: TaskService, mock_session_store: AsyncMock
) -> None:
    """session 的 metadata 应标记 session_type="scheduled"。"""
    await task_service.start()

    await task_service.create_task(
        name="test_task",
        trigger=IntervalTrigger(seconds=9999),
        agent_id="ag_test",
        prompt="test",
    )

    # session_store.save 应被调用，且 session 带 scheduled 标记
    mock_session_store.save.assert_called()
    saved_session = mock_session_store.save.call_args[0][0]
    assert saved_session.metadata["session_type"] == "scheduled"
    assert saved_session.metadata["task_name"] == "test_task"

    await task_service.stop()


async def test_create_task_duplicate_name(task_service: TaskService) -> None:
    await task_service.start()
    await task_service.create_task(
        name="dup", trigger=IntervalTrigger(seconds=60), agent_id="ag", prompt="x"
    )

    with pytest.raises(ValueError, match="already exists"):
        await task_service.create_task(
            name="dup", trigger=IntervalTrigger(seconds=60), agent_id="ag", prompt="y"
        )
    await task_service.stop()


# ---- update_task ----


async def test_update_llm_task(
    task_service: TaskService, mock_session_store: AsyncMock, config_path: Path
) -> None:
    await task_service.start()
    await task_service.create_task(
        name="test_task",
        trigger=IntervalTrigger(seconds=9999),
        agent_id="ag_test",
        prompt="Hello",
    )

    view = await task_service.update_task("test_task", {"prompt": "Updated prompt"})
    assert view["prompt"] == "Updated prompt"

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["tasks"]["test_task"]["prompt"] == "Updated prompt"
    await task_service.stop()


async def test_update_nonexistent_task(task_service: TaskService) -> None:
    await task_service.start()
    with pytest.raises(ValueError, match="not found"):
        await task_service.update_task("no_such", {"prompt": "x"})
    await task_service.stop()


async def test_update_system_task_rejected(task_service: TaskService) -> None:
    await task_service.start()
    sys_def = TaskDefinition(
        name="sys_task",
        trigger=IntervalTrigger(seconds=60),
        handler="some.func",
    )
    task_service._definitions["sys_task"] = sys_def

    with pytest.raises(ValueError, match="System task cannot be modified"):
        await task_service.update_task("sys_task", {"prompt": "x"})
    await task_service.stop()


# ---- toggle_task ----


async def test_toggle_task(
    task_service: TaskService, mock_session_store: AsyncMock, config_path: Path
) -> None:
    await task_service.start()
    await task_service.create_task(
        name="test_task",
        trigger=IntervalTrigger(seconds=9999),
        agent_id="ag_test",
        prompt="Hello",
    )

    view = await task_service.toggle_task("test_task", False)
    assert view["enabled"] is False
    await task_service.stop()


# ---- trigger_task ----


async def test_trigger_task(task_service: TaskService, mock_session_store: AsyncMock) -> None:
    await task_service.start()
    await task_service.create_task(
        name="test_task",
        trigger=IntervalTrigger(seconds=9999),
        agent_id="ag_test",
        prompt="Hello",
    )

    result = await task_service.trigger_task("test_task")
    assert result["success"] is True
    await task_service.stop()


async def test_trigger_nonexistent_task(task_service: TaskService) -> None:
    await task_service.start()
    with pytest.raises(ValueError, match="not found"):
        await task_service.trigger_task("no_such")
    await task_service.stop()


# ---- list_tasks / get_task ----


async def test_list_tasks_with_event_trigger(task_service: TaskService) -> None:
    await task_service.start()
    sys_def = TaskDefinition(
        name="idle_compact",
        trigger=EventTrigger(event_name="session_activity", idle_timeout_seconds=600),
        handler="some.func",
    )
    task_service._definitions["idle_compact"] = sys_def

    tasks = task_service.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["trigger"]["type"] == "event"
    await task_service.stop()


# ---- delete_task ----


async def test_delete_task(
    task_service: TaskService, mock_session_store: AsyncMock, config_path: Path
) -> None:
    """删除任务时同时清理关联 session。"""
    await task_service.start()
    await task_service.create_task(
        name="test_task",
        trigger=IntervalTrigger(seconds=9999),
        agent_id="ag_test",
        prompt="Hello",
    )

    await task_service.delete_task("test_task")
    assert task_service.get_task("test_task") is None

    # 验证 session 被清理
    mock_session_store.delete.assert_called_once()

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "test_task" not in data["tasks"]
    await task_service.stop()


async def test_delete_system_task_rejected(task_service: TaskService) -> None:
    await task_service.start()
    sys_def = TaskDefinition(
        name="sys_task",
        trigger=IntervalTrigger(seconds=60),
        handler="some.func",
    )
    task_service._definitions["sys_task"] = sys_def

    with pytest.raises(ValueError, match="System task cannot be deleted"):
        await task_service.delete_task("sys_task")
    await task_service.stop()


# ---- session 恢复 ----


async def test_start_restores_session_map(
    task_service: TaskService, config_path: Path
) -> None:
    """启动时从 config params 恢复 session_id 映射。"""
    config = {
        "tasks": {
            "remind": {
                "trigger": {"type": "interval", "seconds": 3600},
                "peer_key": "web:default:sched:remind",
                "prompt": "Drink water",
                "params": {"session_id": "sess_restore123", "agent_id": "ag_x"},
                "enabled": True,
            }
        }
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    await task_service.start()
    assert task_service._session_map["remind"] == "sess_restore123"

    tasks = task_service.list_tasks()
    assert tasks[0]["session_id"] == "sess_restore123"
    assert tasks[0]["agent_id"] == "ag_x"
    await task_service.stop()
