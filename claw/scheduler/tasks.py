"""预定义任务：daily distill、periodic memory update、idle auto compact。"""
from __future__ import annotations

from claw.scheduler.context import TaskContext
from claw.scheduler.types import TaskResult


async def daily_memory_distill(ctx: TaskContext) -> TaskResult:
    """全局 task：distill 不依赖 session，直接操作 memory manager。"""
    if ctx.memory_manager is None:
        return TaskResult("daily_memory_distill", False, error="No memory_manager")
    count = await ctx.distill()
    return TaskResult(
        "daily_memory_distill", True,
        message=f"Distilled {count} item(s)",
    )


async def periodic_memory_update(ctx: TaskContext) -> TaskResult:
    """全局 task：遍历所有活跃 session，更新各自的 daily memory。"""
    sessions = await ctx.all_active_sessions()
    if not sessions:
        return TaskResult(
            "periodic_memory_update", True,
            message="Skipped: no active sessions",
        )
    updated = 0
    for _peer_key, session in sessions:
        if await ctx.update_daily(session, force=True):
            updated += 1
    return TaskResult(
        "periodic_memory_update", True,
        message=f"Updated {updated}/{len(sessions)} session(s)",
    )


async def idle_auto_compact(ctx: TaskContext) -> TaskResult:
    """Per-peer task：从 event payload 获取空闲的 peer_key，执行 compact + distill。"""
    peer_key = ctx.last_event_payload("session_activity", key="peer_key")
    if not peer_key:
        return TaskResult(
            "idle_auto_compact", True,
            message="Skipped: no peer from event",
        )
    session = await ctx.active_session(peer_key)
    if session is None:
        return TaskResult(
            "idle_auto_compact", True,
            message=f"Skipped: no active session for {peer_key}",
        )
    summary = await ctx.compact(peer_key)
    if summary is None:
        return TaskResult(
            "idle_auto_compact", True,
            message=f"Nothing to compact for {peer_key}",
        )
    await ctx.distill()
    return TaskResult(
        "idle_auto_compact", True,
        message=f"Compacted {peer_key}: {summary[:80]}...",
    )
