"""CLI 聊天入口：读取终端输入，调用 MiniClaw 运行时，打印回复。"""

from __future__ import annotations

from dotenv import load_dotenv

from claw.agent import MiniClaw


def run(claw: MiniClaw | None = None) -> None:
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

        reply = claw.reply(text)
        if reasoning := reply.metadata.get("reasoning"):
            print(f"think> {reasoning}")
        print(f"claw> {reply.text}")


if __name__ == "__main__":
    run()
