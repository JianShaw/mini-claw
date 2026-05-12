"""运行时网关：回答三个核心问题——哪个会话？哪个 Agent？回复发到哪？"""

from __future__ import annotations

from claw.ports import AgentRunner, Delivery, SessionStore
from claw.session import build_session_key, create_session
from claw.types import AgentReply, InboundMessage


class RuntimeGateway:
    """核心编排模块，协调会话查找/创建、Agent 执行、回复投递。

    依赖通过构造函数注入（SessionStore / AgentRunner / Delivery），
    不直接 import 任何具体实现。
    """

    def __init__(
        self,
        session_store: SessionStore,
        agent_runner: AgentRunner,
        delivery: Delivery,
        default_agent_id: str = "default-agent",
    ) -> None:
        self._session_store = session_store
        self._agent_runner = agent_runner
        self._delivery = delivery
        self._default_agent_id = default_agent_id

    async def handle_inbound_message(self, message: InboundMessage) -> AgentReply:
        # 1. 根据 session_key 查找已有会话，没有则新建
        session_key = build_session_key(message)
        session = await self._session_store.get(session_key)
        if session is None:
            session = create_session(message, agent_id=self._default_agent_id)

        # 2. 调用 Agent 生成回复（Runner 会往 session.history 追加记录）
        reply = await self._agent_runner.run(session, message)

        # 3. 将 session_id 写入 message metadata，供 Delivery 使用
        message.metadata["session_id"] = session.session_id

        # 4. 保存更新后的会话状态
        await self._session_store.save(session)

        # 5. 通过 Delivery 投递回复
        await self._delivery.send(message, reply)

        return reply
