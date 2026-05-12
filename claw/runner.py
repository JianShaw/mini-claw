"""Agent 运行器：当前为 Echo 实现，验证整条链路可用。后续可替换为真实 LLM 调用。"""

from __future__ import annotations

from claw.types import AgentReply, ChatMessage, InboundMessage, Session


class EchoAgentRunner:
    """回声 Agent：将用户输入原样返回，同时维护对话历史。

    用于在不需要网络/LLM 的情况下验证整个运行时链路。
    """

    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        # 记录用户消息
        session.history.append(ChatMessage(role="user", content=message.text))
        # 生成回声回复
        reply = AgentReply(text=f"echo: {message.text}")
        # 记录助手回复
        session.history.append(ChatMessage(role="assistant", content=reply.text))
        return reply
