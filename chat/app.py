"""CLI 聊天入口：读取终端输入，调用 MiniClaw 运行时，打印回复。"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from claw.agent import MiniClaw
from claw.types import StreamChunk

# ANSI 灰色文字用于显示 thinking 内容
_THINK_PREFIX = "\033[90m[think] "
_THINK_SUFFIX = "\033[0m"


class _ChunkPrinter:
    """流式 chunk 打印器：跟踪 thinking↔content 状态切换，
    仅在切换时打印前缀/后缀，避免每个 chunk 都重复标记。"""

    def __init__(self) -> None:
        self._in_thinking = False

    def print(self, chunk: StreamChunk) -> None:
        if chunk.type == "thinking":
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


async def run(claw: MiniClaw | None = None) -> None:
    load_dotenv()
    claw = claw or MiniClaw()

    print("Mini Claw chat")
    print("Type /exit to quit.")

    while True:
        text = input("you> ").strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            break

        print("claw> ", end="", flush=True)
        printer = _ChunkPrinter()
        async for chunk in claw.areply_stream(text):
            printer.print(chunk)
        printer.finish()
        print()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
