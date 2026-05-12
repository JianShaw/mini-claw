# OpenClaw Channel Gateway Runtime 实施方案

> **给执行者：** 按本文档逐个任务实施。每个任务都先写测试，再写最小实现，最后运行对应测试。

**目标：** 构建一个最小版 OpenClaw-style runtime：把外部平台事件统一转换成 `InboundMessage`，通过 Gateway 定位 Session 和 AgentRunner，并通过对应 Delivery 发回回复。

**架构：** 平台差异留在 Channel 层，Agent 与会话逻辑放在 Gateway 之后。`Transport` 负责接收外部事件，`ChannelProcessor` 负责日志、去重、过滤、适配转换、校验和 Gateway 转交，Gateway 负责 session 解析、agent 选择、agent 执行和回复投递。

**技术栈：** Python 3.11、`dataclasses`、`typing.Protocol`、第一版使用内存存储、`pytest` 做测试、`uv` 管理依赖和运行命令。

---

## 目标结构

当前项目只有一个 CLI 包装层 `chat/` 和一个占位的 `MiniClaw`。本方案把 `claw/` 扩展成一个小型 runtime：

```text
外部平台 / CLI
  -> Transport
  -> PlatformEvent
  -> ChannelProcessor
  -> Adapter
  -> InboundMessage
  -> Gateway
  -> SessionStore
  -> AgentRunner
  -> Delivery
```

第一版不要接入真实飞书或 Telegram API。先把接口、内存实现、本地 channel 跑通，后续新增 channel 时再扩展。

---

## 模块分层

### 1. Domain Types

**文件：**
- 新建：`claw/types.py`
- 测试：`tests/test_types.py`

**职责：** 定义所有模块共享的内部标准协议。

**输入：** Python 构造参数，或者 Adapter 转换后的字段。

**输出：** Processor、Gateway、SessionStore、AgentRunner、Delivery 之间流转的 dataclass 对象。

**核心对象：**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MessageType = Literal["text", "image", "file", "mixed"]
Role = Literal["user", "assistant", "tool", "system"]


@dataclass(slots=True)
class PlatformEvent:
    platform: str
    transport: str
    event_id: str
    received_at: int
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class InboundMessage:
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
    role: Role
    content: str


@dataclass(slots=True)
class Session:
    session_id: str
    session_key: str
    channel: str
    account_id: str
    peer_id: str
    sender_id: str
    agent_id: str
    history: list[ChatMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentReply:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

**命名约定：** Python 内部统一使用 `snake_case`。如果外部平台或 TypeScript 层使用 `camelCase`，由 Adapter 负责转换。

**测试：**
- `test_inbound_message_defaults_metadata_to_empty_dict`
- `test_session_defaults_history_to_empty_list`
- `test_agent_reply_defaults_metadata_to_empty_dict`

**预期：**
- 默认 dict/list 不能在实例之间共享。
- 关键 ID 字段必须显式传入。

---

### 2. Ports / Interfaces

**文件：**
- 新建：`claw/ports.py`
- 测试：`tests/test_ports_contracts.py`

**职责：** 定义模块边界，不绑定具体实现。

**输入：** Domain Types。

**输出：** Domain Types 或副作用。

**接口：**

```python
from __future__ import annotations

from typing import Protocol

from claw.types import AgentReply, InboundMessage, PlatformEvent, Session


class Adapter(Protocol):
    def to_inbound_message(self, event: PlatformEvent) -> InboundMessage:
        ...


class Gateway(Protocol):
    async def handle_inbound_message(self, message: InboundMessage) -> AgentReply:
        ...


class DedupeStore(Protocol):
    async def exists(self, key: str) -> bool:
        ...

    async def set(self, key: str, ttl_seconds: int | None = None) -> None:
        ...


class SessionStore(Protocol):
    async def get(self, session_key: str) -> Session | None:
        ...

    async def save(self, session: Session) -> None:
        ...


class AgentRunner(Protocol):
    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        ...


class Delivery(Protocol):
    async def send(self, message: InboundMessage, reply: AgentReply) -> None:
        ...
```

**测试：**
- 用小型 fake class 实现这些 protocol，确认可以导入和调用。
- 这里不需要复杂行为测试，主要验证接口形状。

---

### 3. Session Key 策略与 Session Store

**文件：**
- 新建：`claw/session.py`
- 测试：`tests/test_session.py`

**职责：** 根据 `InboundMessage` 定位稳定会话，并提供会话存储。

**输入：**
- `InboundMessage`
- 默认 `agent_id`

**输出：**
- `session_key`
- `Session`

**实现形态：**

```python
from __future__ import annotations

from uuid import uuid4

from claw.types import InboundMessage, Session


def build_session_key(message: InboundMessage) -> str:
    return f"{message.channel}:{message.account_id}:{message.peer_id}"


def create_session(message: InboundMessage, agent_id: str = "default-agent") -> Session:
    session_key = build_session_key(message)
    return Session(
        session_id=f"sess_{uuid4().hex}",
        session_key=session_key,
        channel=message.channel,
        account_id=message.account_id,
        peer_id=message.peer_id,
        sender_id=message.sender_id,
        agent_id=agent_id,
        metadata={
            "channel": message.channel,
            **message.metadata,
        },
    )


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def get(self, session_key: str) -> Session | None:
        return self._sessions.get(session_key)

    async def save(self, session: Session) -> None:
        self._sessions[session.session_key] = session
```

**测试：**
- `test_build_session_key_uses_channel_account_peer`
- `test_create_session_copies_message_routing_fields`
- `test_create_session_uses_stable_session_key_and_generated_session_id`
- `test_in_memory_session_store_returns_saved_session`
- `test_in_memory_session_store_returns_none_for_missing_session`

**约束：** `session_key` 是业务定位键，`session_id` 是内部 ID。进入 Gateway 的消息应该通过 `session_key` 查找会话。

---

### 4. ToolsRegistry 与 SkillsRegistry

**文件：**
- 新建：`claw/tools.py`
- 新建：`claw/skills.py`
- 测试：`tests/test_tools.py`
- 测试：`tests/test_skills.py`

**职责：** 预留工具和技能扩展点，但第一版不实现完整 LLM tool calling。

**输入：**
- 工具名称、描述、handler。
- 技能名称、描述、instructions。

**输出：**
- 注册后的工具或技能元数据。
- 按名称查找结果。

**测试：**
- `test_tools_registry_registers_and_gets_tool`
- `test_tools_registry_rejects_duplicate_tool_names`
- `test_tools_registry_lists_registered_tools`
- `test_skills_registry_registers_and_gets_skill`
- `test_skills_registry_rejects_duplicate_skill_names`
- `test_skills_registry_lists_registered_skills`

**范围控制：** 第一版只做注册、查找、列表、重复注册保护。暂不实现权限、JSON schema、真实工具执行策略。

---

### 5. AgentRunner

**文件：**
- 新建：`claw/runner.py`
- 测试：`tests/test_runner.py`

**职责：** 封装面向模型的行为。第一版用确定性的 echo runner，避免测试依赖网络或模型服务。

**输入：**
- `Session`
- `InboundMessage`

**输出：**
- `AgentReply`
- 更新后的 `Session.history`

**实现形态：**

```python
from __future__ import annotations

from claw.types import AgentReply, ChatMessage, InboundMessage, Session


class EchoAgentRunner:
    async def run(self, session: Session, message: InboundMessage) -> AgentReply:
        session.history.append(ChatMessage(role="user", content=message.text))
        reply = AgentReply(text=f"echo: {message.text}")
        session.history.append(ChatMessage(role="assistant", content=reply.text))
        return reply
```

**测试：**
- `test_echo_agent_runner_returns_echo_reply`
- `test_echo_agent_runner_appends_user_and_assistant_history`
- `test_echo_agent_runner_preserves_existing_history`

**后续扩展：** 真正的 LLM runner 应该读取 `session.history`、可用 tools、选中的 skills，但仍然实现同一个 `AgentRunner` 接口。

---

### 6. Gateway

**文件：**
- 新建：`claw/gateway.py`
- 测试：`tests/test_gateway.py`

**职责：** 回答 Gateway 的三个核心问题：

```text
这条消息应该进入哪个会话？
这个会话应该由哪个 Agent 处理？
结果应该发回哪里？
```

**输入：**
- `InboundMessage`
- `SessionStore`
- `AgentRunner`
- `Delivery`

**输出：**
- `AgentReply`
- 保存后的 session
- Delivery 发送副作用

**实现形态：**

```python
from __future__ import annotations

from claw.ports import AgentRunner, Delivery, SessionStore
from claw.session import build_session_key, create_session
from claw.types import AgentReply, InboundMessage


class RuntimeGateway:
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
        session_key = build_session_key(message)
        session = await self._session_store.get(session_key)
        if session is None:
            session = create_session(message, agent_id=self._default_agent_id)

        reply = await self._agent_runner.run(session, message)

        await self._session_store.save(session)
        await self._delivery.send(message, reply)

        return reply
```

**测试：**
- `test_gateway_creates_session_when_missing`
- `test_gateway_reuses_existing_session`
- `test_gateway_calls_agent_runner_with_session_and_message`
- `test_gateway_saves_session_after_agent_run`
- `test_gateway_sends_reply_through_delivery`
- `test_gateway_returns_agent_reply`

**设计说明：** 第一版直接注入一个 Delivery。后续如果有多个 channel，可以增加 `DeliveryRegistry`，按 `message.channel` 选择 Delivery。

---

### 7. ChannelProcessor

**文件：**
- 新建：`claw/processor.py`
- 测试：`tests/test_processor.py`

**职责：** 把 `PlatformEvent` 转成经过校验的 `InboundMessage`，并安全地交给 Gateway。

**输入：**
- `PlatformEvent`
- `Adapter`
- `Gateway`
- `DedupeStore`

**输出：**
- `AgentReply | None`
- 当消息合法且未重复时调用 Gateway

**核心行为：**
- 生成去重 key：`platform:event_id`
- 重复事件直接返回 `None`
- 调用 Adapter 转换成 `InboundMessage`
- 把 `transport`、`event_id`、`received_at` 合并进 `metadata`
- 校验 `channel/account_id/peer_id/sender_id/message_id/text`
- 忽略机器人自己发的消息和系统事件
- 捕获异常，避免 webhook 场景反复重试造成重复回复

**测试：**
- `test_processor_dedupes_by_platform_and_event_id`
- `test_processor_sets_dedupe_key_before_gateway`
- `test_processor_adds_event_metadata_to_inbound_message`
- `test_processor_rejects_missing_required_ids`
- `test_processor_rejects_empty_text_message`
- `test_processor_ignores_bot_messages`
- `test_processor_ignores_system_events`
- `test_processor_calls_gateway_for_valid_message`
- `test_processor_returns_gateway_reply`
- `test_processor_catches_adapter_errors`
- `test_processor_catches_gateway_errors`

---

### 8. Local Channel

**文件：**
- 新建：`claw/channels/__init__.py`
- 新建：`claw/channels/local.py`
- 测试：`tests/test_local_channel.py`

**职责：** 提供一个不依赖网络的本地 channel，供 CLI 和测试使用。

**输入：**
- CLI 文本 payload 包装成的 `PlatformEvent`
- `AgentReply`

**输出：**
- `InboundMessage`
- 本地记录的发送结果

**实现对象：**
- `LocalAdapter`：把本地 payload 转成 `InboundMessage`
- `LocalDelivery`：把发送内容记录到内存列表中

**测试：**
- `test_local_adapter_maps_payload_to_inbound_message`
- `test_local_adapter_uses_local_defaults`
- `test_local_delivery_records_sent_reply`

**新增真实 channel 时的推荐结构：**

```text
claw/channels/feishu/
  transport.py
  adapter.py
  processor.py
  delivery.py
  config.py
  types.py
```

当前项目先用 `local.py`，避免过早拆太细。

---

### 9. MiniClaw Facade

**文件：**
- 修改：`claw/agent.py`
- 测试：`tests/test_agent.py`

**职责：** 保留现有 `MiniClaw.reply(text)` API，但内部改成走完整 runtime。

**输入：**
- CLI 传入的普通文本。

**输出：**
- 回复文本。

**测试：**
- `test_mini_claw_reply_keeps_existing_echo_behavior`
- `test_mini_claw_areply_routes_through_delivery`
- `test_mini_claw_preserves_history_across_messages`

**兼容性要求：** `chat.app` 尽量不改，仍然调用 `MiniClaw.reply(text)`。

---

### 10. CLI Smoke Test

**文件：**
- 测试：`tests/test_chat_app.py`
- 可选修改：`chat/app.py`

**职责：** 确保现有终端入口还能正常使用。

**输入：**
- mock 后的 `input()`
- 捕获 stdout

**输出：**
- 打印出的提示文案和 agent 回复。

**测试：**
- `test_chat_app_exits_on_exit_command`
- `test_chat_app_prints_agent_reply_for_user_message`

---

## 实施顺序

### Task 1：添加 pytest

**文件：**
- 修改：`pyproject.toml`
- 修改：`uv.lock`

**步骤：**

```powershell
uv add --dev pytest
uv run pytest
```

**预期：** pytest 可以运行。

---

### Task 2：实现 Domain Types

**文件：**
- 新建：`claw/types.py`
- 新建：`tests/test_types.py`

**步骤：**
1. 先写默认 dict/list 不共享的失败测试。
2. 实现 dataclass。
3. 运行：

```powershell
uv run pytest tests/test_types.py -v
```

---

### Task 3：实现 Ports

**文件：**
- 新建：`claw/ports.py`
- 新建：`tests/test_ports_contracts.py`

**步骤：**
1. 写 fake implementation 测试。
2. 实现 Protocol。
3. 运行：

```powershell
uv run pytest tests/test_ports_contracts.py -v
```

---

### Task 4：实现 Session

**文件：**
- 新建：`claw/session.py`
- 新建：`tests/test_session.py`

**步骤：**
1. 测试 `build_session_key`、`create_session`、`InMemorySessionStore`。
2. 实现 session 相关逻辑。
3. 运行：

```powershell
uv run pytest tests/test_session.py -v
```

---

### Task 5：实现 Tools 和 Skills Registry

**文件：**
- 新建：`claw/tools.py`
- 新建：`claw/skills.py`
- 新建：`tests/test_tools.py`
- 新建：`tests/test_skills.py`

**步骤：**
1. 写注册、查找、重复注册、列表测试。
2. 实现简单 registry。
3. 运行：

```powershell
uv run pytest tests/test_tools.py tests/test_skills.py -v
```

---

### Task 6：实现 AgentRunner

**文件：**
- 新建：`claw/runner.py`
- 新建：`tests/test_runner.py`

**步骤：**
1. 写 echo 回复和 history 追加测试。
2. 实现 `EchoAgentRunner`。
3. 运行：

```powershell
uv run pytest tests/test_runner.py -v
```

---

### Task 7：实现 Gateway

**文件：**
- 新建：`claw/gateway.py`
- 新建：`tests/test_gateway.py`

**步骤：**
1. 用 fake store、fake runner、fake delivery 写测试。
2. 实现 `RuntimeGateway`。
3. 运行：

```powershell
uv run pytest tests/test_gateway.py -v
```

---

### Task 8：实现 Processor

**文件：**
- 新建：`claw/processor.py`
- 新建：`tests/test_processor.py`

**步骤：**
1. 写去重、校验、忽略规则、metadata 注入、异常捕获测试。
2. 实现 `InMemoryDedupeStore` 和 `ChannelProcessor`。
3. 运行：

```powershell
uv run pytest tests/test_processor.py -v
```

---

### Task 9：实现 Local Channel

**文件：**
- 新建：`claw/channels/__init__.py`
- 新建：`claw/channels/local.py`
- 新建：`tests/test_local_channel.py`

**步骤：**
1. 写 adapter 映射和 delivery 记录测试。
2. 实现 `LocalAdapter` 和 `LocalDelivery`。
3. 运行：

```powershell
uv run pytest tests/test_local_channel.py -v
```

---

### Task 10：把 MiniClaw 接入 Runtime

**文件：**
- 修改：`claw/agent.py`
- 新建：`tests/test_agent.py`

**步骤：**
1. 写 `reply()` 兼容性测试和 `areply()` 测试。
2. 用 local runtime 替换原来的 echo 占位逻辑。
3. 运行：

```powershell
uv run pytest tests/test_agent.py -v
```

---

### Task 11：添加 CLI Smoke Test

**文件：**
- 新建：`tests/test_chat_app.py`
- 可选修改：`chat/app.py`

**步骤：**
1. 用 `monkeypatch` mock `input()`。
2. 用 `capsys` 捕获输出。
3. 运行：

```powershell
uv run pytest tests/test_chat_app.py -v
```

---

### Task 12：完整验证

**运行全部测试：**

```powershell
uv run pytest -v
```

**手动运行 CLI：**

```powershell
uv run mini-claw-chat
```

**预期：**

```text
Mini Claw chat
Type /exit to quit.
you> hello
claw> echo: hello
```

---

## 测试矩阵

| 模块 | 主要输入 | 主要输出 | 必测点 |
| --- | --- | --- | --- |
| `claw.types` | 构造参数 | domain dataclass | 默认值隔离、必填字段 |
| `claw.ports` | domain 对象 | protocol contract | fake implementation 可运行 |
| `claw.session` | `InboundMessage` | `session_key`、`Session` | key 生成、创建 session、存取 |
| `claw.tools` | `Tool` | lookup/list | 注册、重复保护、列表 |
| `claw.skills` | `Skill` | lookup/list | 注册、重复保护、列表 |
| `claw.runner` | `Session`、`InboundMessage` | `AgentReply`、history | echo、history 追加 |
| `claw.gateway` | `InboundMessage` | reply、保存 session、delivery 调用 | 新建/复用 session、runner、delivery |
| `claw.processor` | `PlatformEvent` | reply 或 `None` | 去重、校验、忽略、metadata、异常 |
| `claw.channels.local` | local payload、reply | `InboundMessage`、发送记录 | adapter 映射、delivery 记录 |
| `claw.agent` | text | reply text | 兼容旧 API、history |
| `chat.app` | 终端输入 | 打印回复 | CLI smoke test |

---

## 后续扩展

实现完第一版后，新增真实 channel 时建议补齐：

```text
transport.py  # HTTP webhook / WebSocket / Polling / MQ
adapter.py    # 平台 payload -> InboundMessage
processor.py  # channel-specific processor wiring
delivery.py   # AgentReply -> 平台发送 API
config.py     # token、secret、app id、开关
types.py      # 平台原始事件类型
```

Gateway 不应该因为普通 channel 增加而频繁变化。如果 Gateway 里开始出现大量平台判断，应该把逻辑移回 Adapter、Delivery 或 registry。

---

## 暂定决策

第一版使用以下默认：

```text
session_key = channel:account_id:peer_id
agent_id = default-agent
delivery = 注入的 local delivery
processor errors = 捕获后返回 None
```

后续需要再决定：

- 群聊 session 是按群共享，还是按群内 sender 拆分。
- session key 是否加入 thread/topic ID。
- 多 Agent 是通过配置、mention、metadata，还是 LLM routing 选择。
- Delivery 是直接注入，还是通过 `DeliveryRegistry` 按 channel 选择。
- Processor 错误是完全吞掉，还是交给 logger/error sink。
