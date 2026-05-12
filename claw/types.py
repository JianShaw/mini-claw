"""领域类型：所有模块共用的数据结构定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 消息类型：纯文本、图片、文件、混合
MessageType = Literal["text", "image", "file", "mixed"]
# 对话角色：用户、助手、工具调用结果、系统提示
Role = Literal["user", "assistant", "tool", "system"]


@dataclass(slots=True)
class PlatformEvent:
    """外部平台原始事件，由 Transport 层产出。

    platform:    来源平台标识（local / feishu / telegram）
    transport:   传输方式（cli / webhook / websocket）
    event_id:    平台侧事件唯一 ID，用于去重
    received_at: 接收时间戳（毫秒）
    payload:     平台原始数据，结构因平台而异
    headers:     可选的 HTTP 头等信息
    """
    platform: str
    transport: str
    event_id: str
    received_at: int
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class InboundMessage:
    """Adapter 转换后的标准化消息，所有内部模块只认这个类型。

    channel:      频道标识（local / feishu / telegram）
    account_id:   机器人账号 ID（一个平台可能有多个机器人）
    peer_id:      对话对方 ID（用户或群组）
    sender_id:    实际发送者 ID（群聊中可能与 peer_id 不同）
    message_id:   消息唯一 ID
    text:         消息文本内容
    timestamp:    消息时间戳
    message_type: 消息类型
    raw:          保留原始平台数据，方便需要时回查
    metadata:     扩展元数据（由 Processor 补充 transport/event_id 等信息）
    """
    channel: str
    account_id: str
    peer_id: str
    sender_id: str
    message_id: str
    text: str
    timestamp: int
    message_type: MessageType
    raw: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatMessage:
    """对话历史中的单条消息。"""
    role: Role
    content: str
    ts: int | None = None


@dataclass(slots=True)
class Session:
    """会话状态：贯穿整个对话生命周期的上下文载体。

    session_id:   系统生成的唯一 ID（sess_xxx）
    session_key:  业务路由键（channel:account_id:peer_id），用于查找会话
    history:      对话历史，AgentRunner 每轮追加 user + assistant 消息
    """
    session_id: str
    session_key: str
    channel: str
    account_id: str
    peer_id: str
    sender_id: str
    agent_id: str
    history: list[ChatMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    summary: str | None = None


@dataclass(slots=True)
class AgentReply:
    """Agent 的回复结果。"""
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamChunk:
    """流式输出中的一个片段，通过 type 区分思考和正文。"""
    type: Literal["thinking", "content"]
    text: str
