"""TaskRunHistory 测试：追加写入、读取、按任务名过滤。"""
from __future__ import annotations

from pathlib import Path

from claw.scheduler.history import TaskRunHistory
from claw.scheduler.types import TaskRunRecord


def _record(task_name: str = "test", **overrides: object) -> TaskRunRecord:
    defaults = {
        "task_name": task_name,
        "triggered_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:00:01",
        "success": True,
        "task_type": "system",
        "message": "ok",
        "error": None,
    }
    defaults.update(overrides)
    return TaskRunRecord(**defaults)  # type: ignore[arg-type]


async def test_record_appends_to_file(tmp_path: Path) -> None:
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    history.record(_record("task_a"))
    history.record(_record("task_b"))
    records = history.list_recent()
    assert len(records) == 2
    assert records[0].task_name == "task_a"
    assert records[1].task_name == "task_b"


async def test_list_recent_respects_limit(tmp_path: Path) -> None:
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    for i in range(10):
        history.record(_record(f"task_{i}"))
    records = history.list_recent(limit=3)
    assert len(records) == 3
    assert records[0].task_name == "task_7"
    assert records[2].task_name == "task_9"


async def test_list_recent_empty_file(tmp_path: Path) -> None:
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    records = history.list_recent()
    assert records == []


async def test_get_by_task_filters_correctly(tmp_path: Path) -> None:
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    history.record(_record("task_a"))
    history.record(_record("task_b"))
    history.record(_record("task_a"))
    records = history.get_by_task("task_a")
    assert len(records) == 2
    assert all(r.task_name == "task_a" for r in records)


async def test_get_by_task_respects_limit(tmp_path: Path) -> None:
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    for i in range(5):
        history.record(_record("task_a", message=f"run {i}"))
    records = history.get_by_task("task_a", limit=2)
    assert len(records) == 2


async def test_record_preserves_error_field(tmp_path: Path) -> None:
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    history.record(_record("fail_task", success=False, error="timeout"))
    records = history.list_recent()
    assert len(records) == 1
    assert records[0].success is False
    assert records[0].error == "timeout"


async def test_record_preserves_task_type(tmp_path: Path) -> None:
    history = TaskRunHistory(path=tmp_path / "history.jsonl")
    history.record(_record("llm_task", task_type="llm"))
    records = history.list_recent()
    assert records[0].task_type == "llm"
