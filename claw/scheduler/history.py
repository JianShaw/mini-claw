"""TaskRunHistory：追加写入的 JSONL 任务执行记录存储。"""
from __future__ import annotations

import json
from pathlib import Path

from claw.scheduler.types import TaskRunRecord


class TaskRunHistory:
    """追加写入 JSONL 文件，记录每次定时任务的执行结果。"""

    def __init__(self, path: str | Path = "data/scheduler/history.jsonl") -> None:
        self._path = Path(path)

    def record(self, run: TaskRunRecord) -> None:
        """追加一条执行记录到 JSONL 文件。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "task_name": run.task_name,
            "triggered_at": run.triggered_at,
            "completed_at": run.completed_at,
            "success": run.success,
            "task_type": run.task_type,
            "message": run.message,
            "error": run.error,
        }, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def list_recent(self, limit: int = 20) -> list[TaskRunRecord]:
        """返回最近 limit 条执行记录（按写入顺序，最新在最后）。"""
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().split("\n")
        records: list[TaskRunRecord] = []
        for line in lines[-limit:]:
            if not line.strip():
                continue
            data = json.loads(line)
            records.append(TaskRunRecord(**data))
        return records

    def get_by_task(self, task_name: str, limit: int = 10) -> list[TaskRunRecord]:
        """返回指定任务的最近 limit 条执行记录。"""
        recent = self.list_recent(limit=200)
        return [r for r in recent if r.task_name == task_name][:limit]
