# 流式输出（Streaming）实现方案

> 日期：2026-05-12
> 状态：已实现，已扩展 thinking 流式支持

## Context

当前 `DeepSeekAgentRunner.run()` 使用 `await client.chat.completions.create()` 一次性等待完整回复。
用户在 CLI 里必须等模型全部生成完才能看到输出，体验差。

目标：让 AgentRunner 流式产出文本，CLI 逐字显示，同时保持现有非流式路径不动。

核心原则：**chunk 是临时显示，done 后的完整内容才是 message**。

## 方案：async generator yield str

不引入复杂事件类型（`StreamEvent` 等），直接用 Python async generator yield 字符串。
当前只有一种消费者（终端），yield `str` 就够了。

---

## 改动清单

### 1. `claw/runner.py` — EchoAgentRunner 加 `run_stream()`

```python
async def run_stream(self, session, message) -> AsyncIterator[str]:
    session.history.append(ChatMessage(role="user", content=message.text))
    text = f"echo: {message.text}"
    yield text
    # 不追加 assistant — 调用方（Gateway）负责
```

Echo runner 没有网络延迟，一次 yield 全文。

### 2. `claw/deepseek.py` — DeepSeekAgentRunner 加 `run_stream()`

```python
async def run_stream(self, session, message) -> AsyncIterator[str]:
    session.history.append(ChatMessage(role="user", content=message.text))
    messages = [{"role": m.role, "content": m.content} for m in session.history]

    kwargs = {"model": self.model, "messages": messages, "stream": True}
    if self.thinking:
        kwargs["reasoning_effort"] = "high"

    stream = await self.client.chat.completions.create(**kwargs)
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta
    # 不追加 assistant — 调用方负责
```

### 3. `claw/gateway.py` — RuntimeGateway 加 `handle_stream()`

```python
from collections.abc import AsyncIterator

async def handle_stream(self, message: InboundMessage) -> AsyncIterator[str]:
    session_key = build_session_key(message)
    session = await self._session_store.get(session_key)
    if session is None:
        session = create_session(message, agent_id=self._default_agent_id)

    full_text = ""
    async for chunk in self._agent_runner.run_stream(session, message):
        full_text += chunk
        yield chunk

    # 流结束，保存完整 assistant message
    session.history.append(ChatMessage(role="assistant", content=full_text))
    message.metadata["session_id"] = session.session_id
    await self._session_store.save(session)
    await self._delivery.send(message, AgentReply(text=full_text))
```

### 4. `claw/processor.py` — ChannelProcessor 加 `process_stream()`

复用现有去重/校验/过滤逻辑，只把最后的网关调用换成流式版本：

```python
async def process_stream(self, event: PlatformEvent) -> AsyncIterator[str]:
    try:
        dedupe_key = self._get_dedupe_key(event)
        if await self._dedupe_store.exists(dedupe_key):
            return
        await self._dedupe_store.set(dedupe_key, ttl_seconds=60 * 60)

        inbound = self._adapter.to_inbound_message(event)
        inbound.metadata = {
            **inbound.metadata,
            "transport": event.transport,
            "event_id": event.event_id,
            "received_at": event.received_at,
        }

        if self._validate(inbound) is not None:
            return
        if self._should_ignore(inbound):
            return

        async for chunk in self._gateway.handle_stream(inbound):
            yield chunk
    except Exception:
        return
```

### 5. `claw/agent.py` — MiniClaw 加 `areply_stream()`

```python
from collections.abc import AsyncIterator

async def areply_stream(self, text: str) -> AsyncIterator[str]:
    event = self.transport.receive(text)
    async for chunk in self.processor.process_stream(event):
        yield chunk
```

### 6. `chat/app.py` — 改为 async，消费流式输出

```python
import asyncio

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
        async for chunk in claw.areply_stream(text):
            print(chunk, end="", flush=True)
        print()

if __name__ == "__main__":
    asyncio.run(run())
```

### 7. 不改的文件

- `claw/ports.py` — Protocol 不变，`run_stream()` 和 `handle_stream()` 用 duck typing
- `claw/types.py` — 不加 StreamEvent 类型
- `claw/channels/local.py` — Delivery 接口不变，仍接收完整 `AgentReply`
- 现有非流式路径（`run()` / `reply()` / `handle_inbound_message()`）完全不动

---

## 设计决策

### 为什么 Runner 不追加 assistant 到 history？

`run_stream()` 是 generator，yield 后控制权在调用方。如果 CLI 中途 Ctrl+C，generator 被关闭，Runner 内的 append 不会执行。Gateway 是唯一知道"流正常结束"的地方，在那里保存最安全。

### 为什么不改 Protocol？

Python duck typing 够用。`run_stream()` 和 `handle_stream()` 是具体类的方法，不需要 Protocol 约束。避免为流式单独建一套接口体系。

### 为什么不加 StreamEvent 类型？

当前只有一种消费者（终端），yield `str` 就够了。等未来有 WebSocket/SSE 多消费者时再引入事件类型。

### 为什么 Delivery 不感知流式？

Delivery 只关心完整消息（记录到文件 / 调用 API），流式显示是 CLI 的职责。

---

## 测试影响

### 现有测试：需要修改的

| 测试文件 | 需要改动 | 原因 |
|---|---|---|
| `tests/test_chat_app.py` | `run()` 变成 async，测试需适配 | 现有测试调用同步 `run()`，改为 `asyncio.run(run(...))` |
| `tests/test_processor.py` | `BrokenGateway` mock 需加 `handle_stream` | 测试"Gateway 抛异常时返回 None"，流式版本也需要同样兜底 |

### 现有测试：不需要修改的

| 测试文件 | 原因 |
|---|---|
| `tests/test_runner.py` | 测试 `run()`，非流式路径不动 |
| `tests/test_gateway.py` | 测试 `handle_inbound_message()`，非流式路径不动 |
| `tests/test_agent.py` | 测试 `reply()` / `areply()`，非流式路径不动 |
| `tests/test_ports_contracts.py` | Protocol 没变，Fake 实现不需要 `run_stream` |
| `tests/test_local_channel.py` | 传输/适配/投递逻辑不受影响 |

### 新增测试

#### `tests/test_runner.py` 追加

- `test_echo_agent_runner_stream_yields_text` — 验证 `run_stream()` yield 正确文本
- `test_echo_agent_runner_stream_appends_user_history` — 验证 user 消息被追加到 history
- `test_echo_agent_runner_stream_does_not_append_assistant` — 验证 assistant 不被追加（Gateway 负责）

#### `tests/test_gateway.py` 追加

- `test_gateway_stream_yields_chunks` — 验证 `handle_stream()` yield 文本块
- `test_gateway_stream_saves_complete_assistant_to_history` — 验证流结束后完整 assistant 写入 history
- `test_gateway_stream_saves_session` — 验证流结束后 session 被持久化
- `test_gateway_stream_sends_complete_reply_to_delivery` — 验证 Delivery 收到完整回复
- `test_gateway_stream_reuses_existing_session` — 验证会话复用

#### `tests/test_processor.py` 追加

- `test_processor_stream_yields_chunks_for_valid_event` — 正常事件走通流式
- `test_processor_stream_returns_empty_on_dedupe` — 重复事件不产出 chunk
- `test_processor_stream_returns_empty_on_validation_failure` — 校验失败静默返回
- `test_processor_stream_catches_gateway_stream_errors` — Gateway 流式异常兜底

#### `tests/test_agent.py` 追加

- `test_mini_claw_areply_stream_yields_text` — 验证 `areply_stream()` 产出文本
- `test_mini_claw_areply_stream_saves_history` — 验证流结束后 history 正确
- `test_mini_claw_areply_stream_routes_through_delivery` — 验证 Delivery 被调用

#### `tests/test_chat_app.py` 修改 + 追加

- 现有测试改为 `asyncio.run(run(...))` 适配
- 追加 `test_chat_app_stream_prints_chunks` — 验证流式输出逐块打印

---

## 验证步骤

1. `uv run pytest` — 全部测试通过（现有 + 新增）
2. `uv run mini-claw-chat` — 手动输入，观察逐字输出效果
3. 检查 `data/` 目录 JSONL 文件 — 流式模式下仍正确记录完整对话
