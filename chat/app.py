"""CLI 聊天入口：读取终端输入，调用 MiniClaw 运行时，打印回复。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from dotenv import load_dotenv

_LOG_DIR = Path("logs")

from claw.agent import MiniClaw
from claw.channels.local import LocalDelivery
from claw.tools import ToolsRegistry
from claw.builtin_tools import register_all
from claw.memory import MemoryManager
from claw.types import StreamChunk

# ANSI 灰色文字用于显示 thinking 内容，黄色用于系统通知
_THINK_PREFIX = "\033[90m[think] "
_THINK_SUFFIX = "\033[0m"
_SYSTEM_STYLE = "\033[33m"
_SYSTEM_RESET = "\033[0m"

_COMMANDS_HELP = """\
Commands:
  /new       - 创建新会话
  /sessions  - 列出所有会话
  /select ID - 切换到指定会话
  /delete ID - 删除指定会话
  /compact   - 压缩当前会话上下文
  /mcp       - 显示 MCP 服务器状态
  /help      - 显示帮助
  /exit      - 退出"""

_COMMANDS_HELP += "\n  /memory today|long|update|distill - manage memory files"
_COMMANDS_HELP += "\n  /tasks - list scheduled tasks, /task run <name> - run a task"


class _ChunkPrinter:
    """流式 chunk 打印器：跟踪 thinking↔content 状态切换，
    仅在切换时打印前缀/后缀，避免每个 chunk 都重复标记。"""

    def __init__(self) -> None:
        self._in_thinking = False

    def print(self, chunk: StreamChunk) -> None:
        if chunk.type == "system":
            # 系统通知（压缩事件等），独立一行显示
            print(f"\n{_SYSTEM_STYLE}{chunk.text}{_SYSTEM_RESET}", flush=True)
        elif chunk.type == "tool_call":
            print(f"\n{_SYSTEM_STYLE}[calling {chunk.text} tool...]{_SYSTEM_RESET}", end="", flush=True)
        elif chunk.type == "tool_result":
            print(f"\n{_SYSTEM_STYLE}[tool result: {chunk.text[:80]}...]{_SYSTEM_RESET}", end="", flush=True)
        elif chunk.type == "thinking":
            if not self._in_thinking:
                print(_THINK_PREFIX, end="", flush=True)
                self._in_thinking = True
            print(chunk.text, end="", flush=True)
        else:
            if self._in_thinking:
                print(_THINK_SUFFIX, end="", flush=True)
                self._in_thinking = False
            print(chunk.text, end="", flush=True)

    def finish(self) -> None:
        """流结束时，如果还在 thinking 状态，补上后缀。"""
        if self._in_thinking:
            print(_THINK_SUFFIX, end="", flush=True)
            self._in_thinking = False


async def _handle_command(text: str, claw: MiniClaw) -> bool:
    """处理会话管理命令，返回 True 表示已处理（不需要发送给 Agent）。"""
    active_id = await claw.get_active_session_id()

    if text == "/new":
        session = await claw.new_session()
        print(f"New session: {session.session_id}")
        return True

    if text == "/sessions":
        sessions = await claw.list_sessions()
        if not sessions:
            print("No sessions.")
        else:
            for s in sessions:
                marker = " *" if s.session_id == active_id else ""
                print(f"  {s.session_id}{marker}")
        return True

    if text.startswith("/select"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            print("Usage: /select <session_id>")
        else:
            result = await claw.select_session(parts[1])
            if result:
                print(f"Switched to {result.session_id}")
            else:
                print(f"Session {parts[1]} not found")
        return True

    if text.startswith("/delete"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            print("Usage: /delete <session_id>")
        else:
            await claw.delete_session(parts[1])
            print(f"Deleted {parts[1]}")
        return True

    if text == "/compact":
        summary = await claw.compact_session()
        if summary is None:
            print("No active session.")
        elif summary == "":
            print("Not enough messages to compact yet.")
        else:
            print(f"Compacted. Summary:\n  {summary}")
        return True

    if text == "/help":
        print(_COMMANDS_HELP)
        return True

    if text == "/mcp":
        statuses = claw.get_mcp_status()
        if not statuses:
            print("MCP not configured.")
        else:
            for s in statuses:
                status = "connected" if s.connected else "disconnected"
                tools = f", {s.tool_count} tools" if s.connected else ""
                err = f" ({s.error})" if s.error else ""
                print(f"  {s.name}: {status}{tools}{err}")
        return True

    if text == "/tasks":
        statuses = claw.get_task_status()
        if not statuses:
            print("Scheduler not configured.")
        else:
            for s in statuses:
                status = "enabled" if s["enabled"] else "disabled"
                last = ""
                if s["last_result"]:
                    lr = s["last_result"]
                    last = f" [last: {'OK' if lr.success else 'FAIL'} {lr.message}]"
                print(f"  {s['name']} ({s['trigger_type']}, {status}){last}")
                if s["description"]:
                    print(f"    {s['description']}")
        return True

    if text.startswith("/task run"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            print("Usage: /task run <name>")
        else:
            result = await claw.run_task(parts[2])
            if result is None:
                print("Scheduler not configured or task not found.")
            elif result.success:
                print(f"Task '{result.task_name}' completed: {result.message}")
            else:
                print(f"Task '{result.task_name}' failed: {result.error}")
        return True

    if text.startswith("/memory"):
        parts = text.split(maxsplit=1)
        action = parts[1].strip() if len(parts) > 1 else "today"
        if action == "today":
            content = await claw.memory_today()
            print(content if content else "No daily memory.")
        elif action == "long":
            content = await claw.memory_long()
            print(content if content else "No long-term memory.")
        elif action == "update":
            updated = await claw.update_memory_today()
            print("Daily memory updated." if updated else "No active session to update.")
        elif action == "distill":
            added = await claw.distill_memory()
            print(f"Long-term memory updated. Added {added} item(s).")
        else:
            print("Usage: /memory [today|long|update|distill]")
        return True

    return False


def _make_claw() -> MiniClaw:
    """创建带内置工具的 MiniClaw 实例。"""
    registry = ToolsRegistry()
    register_all(registry)
    delivery = LocalDelivery()
    return MiniClaw(
        delivery=delivery,
        tools_registry=registry,
        mcp_config_path="mcp_config.json",
        memory_manager=MemoryManager(),
        schedule_config_path="schedule_config.json",
    )


async def _print_scheduled_deliveries(
    delivery: LocalDelivery,
    stream_lock: asyncio.Lock,
) -> None:
    """Print scheduled replies delivered while the CLI is waiting for input.

    Uses stream_lock to avoid interleaving with streaming LLM output.
    """
    while True:
        message, reply = await delivery.events.get()
        if not message.metadata.get("scheduled"):
            continue
        text = reply.text.strip()
        if not text:
            continue
        task_name = str(message.metadata.get("task_name") or "scheduled")
        # async with = acquire() 加锁 + 退出时自动 release() 解锁
        # 如果流式回复正在持锁，这里会等待直到流式结束
        async with stream_lock:
            print(f"\nclaw[{task_name}]> {text}\n", flush=True)


async def run(claw: MiniClaw | None = None) -> None:
    load_dotenv()
    claw = claw or _make_claw()

    # 启动 MCP 连接
    await claw.start()
    stream_lock = asyncio.Lock()
    delivery_task: asyncio.Task[None] | None = None
    if isinstance(claw.delivery, LocalDelivery):
        delivery_task = asyncio.create_task(
            _print_scheduled_deliveries(claw.delivery, stream_lock)
        )

    print("Mini Claw chat")
    print("Type /help for commands, /exit to quit.")

    try:
        while True:
            text = (await asyncio.to_thread(input, "you> ")).strip()
            if not text:
                continue
            if text in {"/exit", "/quit"}:
                break

            # 会话管理命令拦截
            if text.startswith("/") and await _handle_command(text, claw):
                continue

            # 持锁期间定时任务不会打断流式输出，退出 with 后自动解锁
            async with stream_lock:
                print("claw> ", end="", flush=True)
                printer = _ChunkPrinter()
                async for chunk in claw.areply_stream(text):
                    printer.print(chunk)
                printer.finish()
                print()

            # 通知调度器有活动，重置空闲计时器
            await claw.emit_event("session_activity", peer_key=claw._current_peer_key())
    finally:
        if delivery_task is not None:
            delivery_task.cancel()
            with suppress(asyncio.CancelledError):
                await delivery_task
        # 停止 MCP 连接
        await claw.stop()


def _setup_logging(*, debug: bool, debug_only: str) -> None:
    """配置日志：控制台按参数决定级别，文件统一写 DEBUG 到 logs/ 目录。"""
    _LOG_DIR.mkdir(exist_ok=True)

    file_handler = logging.FileHandler(
        _LOG_DIR / "mini-claw.log", encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
    ))

    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)

    if debug_only:
        console_level = logging.WARNING
        logging.getLogger(debug_only).setLevel(logging.DEBUG)
    elif debug:
        console_level = logging.DEBUG
    else:
        console_level = logging.WARNING

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(message)s",
    ))
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.DEBUG)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini Claw CLI chat")
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG log output")
    parser.add_argument(
        "--debug-only",
        type=str,
        default="",
        help="Only show DEBUG for specific logger (e.g. claw.scheduler)",
    )
    args = parser.parse_args()

    _setup_logging(debug=args.debug, debug_only=args.debug_only)

    asyncio.run(run())


if __name__ == "__main__":
    main()
