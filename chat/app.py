"""CLI 聊天入口：读取终端输入，调用 MiniClaw 运行时，打印回复。"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from claw.agent import MiniClaw
from claw.tools import ToolsRegistry
from claw.builtin_tools import register_all
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
            print(f"\n{_SYSTEM_STYLE}[calling tool...]{_SYSTEM_RESET}", end="", flush=True)
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
            print("No active session or empty history.")
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

    return False


def _make_claw() -> MiniClaw:
    """创建带内置工具的 MiniClaw 实例。"""
    registry = ToolsRegistry()
    register_all(registry)
    return MiniClaw(tools_registry=registry, mcp_config_path="mcp_config.json")


async def run(claw: MiniClaw | None = None) -> None:
    load_dotenv()
    claw = claw or _make_claw()

    # 启动 MCP 连接
    await claw.start()

    print("Mini Claw chat")
    print("Type /help for commands, /exit to quit.")

    try:
        while True:
            text = input("you> ").strip()
            if not text:
                continue
            if text in {"/exit", "/quit"}:
                break

            # 会话管理命令拦截
            if text.startswith("/") and await _handle_command(text, claw):
                continue

            print("claw> ", end="", flush=True)
            printer = _ChunkPrinter()
            async for chunk in claw.areply_stream(text):
                printer.print(chunk)
            printer.finish()
            print()
    finally:
        # 停止 MCP 连接
        await claw.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
