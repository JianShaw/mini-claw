# Channel 抽象 + Gateway 瘦身重构

> 创建日期: 2026-05-20
> 状态: 待实施

## Context

当前架构问题：
1. **Web 不是标准 Channel** — `web/backend/channel.py` 用裸函数 `web_message()` 而非 Adapter 类，Router 直接参与构造 InboundMessage
2. **Gateway 职责过重** — 承担了 memory/skill 上下文注入（`_inject_memory_context`, `_inject_skill_context`），这些属于 AgentRunner 的职责
3. **后续接 Telegram/飞书时需要复制逻辑** — 没有 Channel 抽象，每个平台入口都要重复类似代码

目标：Gateway 只管"哪个 session、哪个 agent"，AgentRunner 管"这个 agent 看到什么上下文"，Web 跟 Local 一样是标准 Channel。

## Channel 完整管线对比

### Local Channel（现有，完整管线）

```
CLI text
  ↓
LocalTransport.receive(text) → PlatformEvent
  ↓
ChannelProcessor.process(event)
  ├─ DedupeStore 去重
  ├─ LocalAdapter.to_inbound_message(event) → InboundMessage
  ├─ 补充 metadata (transport, event_id, received_at)
  ├─ 校验 (channel, peer_id, sender_id, text 非空)
  ├─ 过滤 (bot 消息, 系统事件)
  └─ Gateway.handle_inbound_message(inbound) → AgentReply
  ↓
Delivery.send(message, reply)
```

### Web Channel（当前，绕过三层）

```
HTTP POST
  ↓
Router 直接调 web_message() → InboundMessage       ← 绕过 Transport + Processor
  ↓
Gateway.handle_stream(inbound) → StreamChunk
  ↓
Router 内联 json.dumps → SSE                        ← 没有独立 Delivery
```

缺失：Transport（没有 PlatformEvent）、Processor（没有去重/校验/过滤）、Delivery（内联 SSE）。

### Web Channel（目标，与 Local 对齐）

```
HTTP POST
  ↓
WebTransport.receive(text, session_id, client_event_id) → PlatformEvent     ← 新增
  ↓
ChannelProcessor.process_stream(event)                                      ← 复用（raise 模式）
  ├─ DedupeStore 去重（按 client_event_id 去重）
  ├─ WebAdapter.to_inbound_message(event) → InboundMessage                 ← 新增
  ├─ 补充 metadata
  ├─ 校验
  ├─ 过滤
  └─ Gateway.handle_stream(inbound) → StreamChunk
  ↓
SseEncoder.encode_chunk(chunk) → SSE event                                  ← 新增
```

### 接 Telegram 时只需写差异层

```
TelegramTransport(request → PlatformEvent)
TelegramAdapter(PlatformEvent → InboundMessage)
TelegramDelivery(StreamChunk → Telegram API call)

ChannelProcessor  ← 完全复用
RuntimeGateway    ← 完全复用
```

## 目标架构

```
Channel (Web/Local/Telegram...)
  ↓ Transport → PlatformEvent
  ↓ ChannelProcessor (共享: dedupe + Adapter + validate + filter)
  ↓ InboundMessage
Gateway (slim: resolve session + resolve agent config)
  ↓ session + message
ContextBuildingAgentRunner(inner_runner, context_builder)  ← wrapper，任何 Runner 都能获得上下文
  ↓ StreamChunk
Delivery / SseEncoder
```

---

## Phase 1: 基础设施（纯新增，不改旧代码）

### 1.1 `claw/ports.py` 更新

**添加 `ContextBuilder` Protocol**：

```python
class ContextBuilder(Protocol):
    """运行时上下文构建器：在 AgentRunner 调用 LLM 前准备记忆和技能上下文。"""
    async def build(self, session: Session, message: InboundMessage) -> None: ...
```

**补充 `Gateway` Protocol 缺失的 `handle_stream`**（Review #8）：

当前 `processor.py:97` 已调用 `self._gateway.handle_stream(...)`，但 `Gateway` Protocol 只定义了 `handle_inbound_message`。补充：

```python
class Gateway(Protocol):
    async def handle_inbound_message(self, message: InboundMessage) -> AgentReply: ...
    async def handle_stream(self, message: InboundMessage) -> AsyncIterator[StreamChunk]: ...
```

### 1.2 `claw/processor.py` 增加 error policy（Review #8）

当前 `process_stream()` 的 `except Exception: return` 会吞掉 `SessionNotFoundError` 等异常，
Web SSE 场景下前端只看到空流 + [DONE]，无法感知错误。

新增 `error_policy` 参数：

```python
from enum import Enum

class ErrorPolicy(Enum):
    """Processor 异常处理策略。"""
    SWALLOW = "swallow"   # 静默返回 None（webhook 场景，避免触发平台重试）
    RAISE = "raise"       # 向上抛异常（Web SSE 场景，Router 可以 encode_error）

class ChannelProcessor:
    def __init__(
        self,
        adapter: Adapter,
        gateway: Gateway,
        dedupe_store: DedupeStore,
        error_policy: ErrorPolicy = ErrorPolicy.SWALLOW,  # 默认保持现有行为
    ) -> None:
        ...

    async def process(self, event: PlatformEvent) -> AgentReply | None:
        try:
            ...
        except Exception:
            if self._error_policy == ErrorPolicy.RAISE:
                raise
            return None

    async def process_stream(self, event: PlatformEvent) -> AsyncIterator[StreamChunk]:
        try:
            ...
        except Exception:
            if self._error_policy == ErrorPolicy.RAISE:
                raise
            return
```

> Local/webhook 场景继续 swallow，Web SSE 场景用 RAISE，Router 外层 catch 后 encode_error。

### 1.3 创建 `claw/channels/web/__init__.py`

空文件。

### 1.4 创建 `claw/channels/web/transport.py`

Web 传输层：将 HTTP 请求参数转为 PlatformEvent。

**关键设计（Review #9）**：支持客户端传入 `client_event_id`，使去重生效：

```python
class WebTransport:
    """Web 传输层：Router 调用 receive() 将请求参数转为标准 PlatformEvent。"""

    def __init__(
        self,
        platform: str = "web",
        transport: str = "http",
        account_id: str = "default",
        peer_id: str = "web",
        sender_id: str = "web",
    ) -> None:
        self._platform = platform
        self._transport = transport
        self._account_id = account_id
        self._peer_id = peer_id
        self._sender_id = sender_id

    def receive(
        self,
        text: str = "",
        *,
        session_id: str | None = None,
        client_event_id: str | None = None,
        extra: dict | None = None,
    ) -> PlatformEvent:
        """将 HTTP 请求参数转为 PlatformEvent。

        Args:
            text: 消息文本
            session_id: 目标会话 ID（Web 端多对话路由用）
            client_event_id: 客户端生成的消息 ID（用于去重），为 None 时 fallback uuid
            extra: 额外 payload 字段
        """
        event_id = client_event_id or f"web-{uuid4().hex[:8]}"
        payload: dict = {
            "account_id": self._account_id,
            "peer_id": self._peer_id,
            "sender_id": self._sender_id,
            "message_id": event_id,
            "text": text,
        }
        if session_id is not None:
            payload["session_id"] = session_id
        if extra:
            payload.update(extra)

        return PlatformEvent(
            platform=self._platform,
            transport=self._transport,
            event_id=event_id,
            received_at=int(time() * 1000),
            payload=payload,
        )
```

> Review #9：同一次客户端重试/双击时 client_event_id 相同，DedupeStore 可正确拦截。
> 无 client_event_id 时 fallback uuid，保持单次请求场景正常。

### 1.5 创建 `claw/channels/web/adapter.py`

Web 适配层：PlatformEvent → InboundMessage。

**关键设计（Review #3 + Review #10）**：从 payload 读取 account_id/peer_id/sender_id，fallback 到默认值。与 LocalAdapter 一致：

```python
WEB_CHANNEL = "web"
WEB_ACCOUNT_ID = "default"
WEB_PEER_ID = "web"
WEB_SENDER_ID = "web"
WEB_PEER_KEY = f"{WEB_CHANNEL}:{WEB_ACCOUNT_ID}:{WEB_PEER_ID}"


class WebAdapter:
    """Web 通道适配器：将 PlatformEvent 转为 InboundMessage。

    与 LocalAdapter 同级，实现 Adapter protocol。
    优先从 payload 读取 account_id/peer_id/sender_id（未来多用户场景），
    无值时 fallback 到模块级默认值（单用户场景）。
    """

    def to_inbound_message(self, event: PlatformEvent) -> InboundMessage:
        payload = event.payload
        metadata: dict = {}
        if payload.get("session_id"):
            metadata["session_id"] = payload["session_id"]
        return InboundMessage(
            channel=WEB_CHANNEL,
            account_id=str(payload.get("account_id", WEB_ACCOUNT_ID)),
            peer_id=str(payload.get("peer_id", WEB_PEER_ID)),
            sender_id=str(payload.get("sender_id", WEB_SENDER_ID)),
            message_id=event.event_id,
            text=str(payload.get("text", "")),
            timestamp=event.received_at,
            message_type="text",
            raw=payload,
            metadata=metadata,
        )

    @staticmethod
    def make_message(
        text: str = "", *, session_id: str | None = None
    ) -> InboundMessage:
        """便捷工厂：构造 PlatformEvent 再委托 to_inbound_message。

        用于 session 管理（create/list/delete）等不需要完整 Transport 管线的场景。
        聊天场景应走 Transport → Processor 完整管线。
        """
        from claw.channels.web.transport import WebTransport

        transport = WebTransport()
        event = transport.receive(text, session_id=session_id)
        return WebAdapter().to_inbound_message(event)
```

> Review #10：不像之前写死常量，而是 payload 优先 + 常量 fallback。
> 单用户场景 Transport 构造的 payload 就含默认值，行为不变。
> 多用户场景 payload 可覆盖 account_id/peer_id，为登录态预留入口。

### 1.6 创建 `claw/channels/web/delivery.py`

Web 投递层：StreamChunk → SSE event 编码。

```python
class SseEncoder:
    """将 StreamChunk 编码为 SSE 事件字典。"""

    @staticmethod
    def encode_chunk(chunk: StreamChunk) -> dict[str, str]:
        data = json.dumps({"type": chunk.type, "text": chunk.text}, ensure_ascii=False)
        return {"data": data}

    @staticmethod
    def encode_error(error: Exception) -> dict[str, str]:
        data = json.dumps({"type": "error", "text": str(error)}, ensure_ascii=False)
        return {"data": data}

    @staticmethod
    def encode_done() -> dict[str, str]:
        return {"data": "[DONE]"}
```

### 1.7 更新 `web/backend/schemas/chat.py`

添加可选的 `client_event_id` 字段（Review #9）：

```python
class ChatStreamRequest(BaseModel):
    session_id: str
    text: str
    client_event_id: str | None = None   # 客户端生成的消息 ID，用于去重
```

### 1.8 创建 `tests/test_web_channel.py`（新，替代旧测试）

测试 Web Channel 完整四件套：

- `TestWebTransport` — receive() 产出 PlatformEvent；有 client_event_id 时使用，无时 fallback uuid
- `TestWebAdapter` — to_inbound_message() 从 payload 读 account/peer/sender；payload 无值时 fallback 常量
- `TestWebAdapterMakeMessage` — make_message() 委托到 to_inbound_message()
- `TestSseEncoder` — encode_chunk()、encode_error()、encode_done()
- `TestProcessorErrorPolicy` — SWALLOW 模式吞异常、RAISE 模式向上抛

### 验证
- 全量测试通过（旧代码未改动，processor.py 增加参数但默认值保持向后兼容）
- 新测试通过

---

## Phase 2: 创建 RuntimeContextBuilder + ContextBuildingAgentRunner wrapper（纯新增）

### 2.1 创建 `claw/agent_runtime/context.py`

从 Gateway 搬出 memory/skill 上下文注入逻辑：

```python
class RuntimeContextBuilder:
    """Agent 运行时上下文构建器：在 AgentRunner 调用 LLM 前准备记忆和技能上下文。"""

    def __init__(self, *, memory_manager=None, skills_registry=None): ...

    async def build(self, session: Session, message: InboundMessage) -> None:
        await self._inject_memory_context(session, message)
        await self._inject_skill_context(session)
```

### 2.2 创建 `claw/agent_runtime/wrapper.py`

```python
class ContextBuildingAgentRunner:
    """AgentRunner wrapper：在 inner runner 执行前注入运行时上下文。"""

    def __init__(self, inner: AgentRunner, context_builder: ContextBuilder): ...

    async def run(self, session, message) -> AgentReply:
        if not message.metadata.get("skip_runtime_context"):
            await self._context_builder.build(session, message)
        return await self._inner.run(session, message)

    async def run_stream(self, session, message) -> AsyncIterator[StreamChunk]:
        if not message.metadata.get("skip_runtime_context"):
            await self._context_builder.build(session, message)
        async for chunk in self._inner.run_stream(session, message):
            yield chunk
```

### 2.3 创建 `tests/test_runtime_context_builder.py`

- 测试 memory context 注入
- 测试 skills listing 注入 + enabled_skills 过滤
- 测试无 manager/registry 时清理 metadata
- 测试 wrapper 的 skip_runtime_context 行为

### 验证
- 全量测试通过
- 新测试通过

---

## Phase 3: Router 迁移 + Gateway 瘦身 + Web Processor 接入

### 3.1 修改 `web/backend/app.py` — 统一注入 Web Processor

**关键设计（Review #8）**：web_processor 在 `_gateway` 确定之后统一创建，无论走默认构建还是外部注入：

```python
def create_app(*, gateway=None, ...) -> FastAPI:
    ...
    # 构建 gateway（如果未注入）
    _gateway = gateway
    if _gateway is None:
        _session_store = SqliteSessionStore(conn)
        _gateway = _build_default_gateway(_agent_store, _session_store)

    # --- 统一在 _gateway 确定后创建 Web Channel 组件 ---
    from claw.channels.web.adapter import WebAdapter
    from claw.channels.web.transport import WebTransport
    from claw.processor import ChannelProcessor, ErrorPolicy, InMemoryDedupeStore

    web_transport = WebTransport()
    web_processor = ChannelProcessor(
        adapter=WebAdapter(),
        gateway=_gateway,
        dedupe_store=InMemoryDedupeStore(),
        error_policy=ErrorPolicy.RAISE,   # Web SSE 需要 Router 外层 catch 并 encode_error
    )

    app.state.gateway = _gateway
    app.state.web_transport = web_transport
    app.state.web_processor = web_processor
    ...
```

> 无论是 `_build_default_gateway()` 还是 `create_app(gateway=...)` 走外部注入，
> 都会在 `_gateway` 确定后统一创建 web_processor，不会遗漏。

### 3.2 修改 `web/backend/routers/chat.py` — 走完整管线

```python
@router.post("/stream")
async def chat_stream(request: ChatStreamRequest, ...) -> EventSourceResponse:
    transport: WebTransport = ...   # 从 app.state 获取
    processor: ChannelProcessor = ...  # 从 app.state 获取

    async def event_generator():
        event = transport.receive(
            request.text,
            session_id=request.session_id,
            client_event_id=request.client_event_id,  # Review #9: 传入客户端 event_id
        )
        # Processor error_policy=RAISE，异常向上抛，Router 外层 catch 后 encode_error
        try:
            async for chunk in processor.process_stream(event):
                yield SseEncoder.encode_chunk(chunk)
        except Exception as e:
            yield SseEncoder.encode_error(e)

        yield SseEncoder.encode_done()

    return EventSourceResponse(event_generator())
```

> Processor 用 RAISE 策略，SessionNotFoundError 等异常不再被吞掉，
> Router 外层 catch 后 encode_error 发给前端。

### 3.3 修改 `web/backend/routers/conversations.py`

session 管理用 `WebAdapter.make_message()`，不走 Processor 管线。

### 3.4 删除 `web/backend/channel.py`

### 3.5 修改 `claw/gateway.py`

- 删除 `_inject_memory_context()` 和 `_inject_skill_context()`
- 删除构造函数的 `skills_registry` 参数
- 保留 `memory_manager`（session 生命周期）
- 保留 `_inject_agent_runtime_profile()`（agent 路由）
- `handle_inbound_message` / `handle_stream` 删除 memory/skill inject 调用
- `_full_compact` 的 temp_msg 设置 `metadata["skip_runtime_context"] = True`

### 3.6 修改 `claw/agent.py`（MiniClaw facade）

**关键设计（Review #8）**：wrapper 接入顺序 — 先用原始 runner 创建 compressor，再包 wrapper：

```python
runner = agent_runner or DeepSeekAgentRunner(api_key=api_key, tools_registry=tools_registry)

# 自动压缩：必须用原始 DeepSeekAgentRunner 创建 compressor
# （isinstance 检查 wrapper 会失败，导致自动压缩静默失效）
compressor = None
if auto_compact and isinstance(runner, DeepSeekAgentRunner):
    from claw.compressor import ContextCompressor
    compressor = ContextCompressor(
        client=runner.client,
        model=runner.model,
        max_tokens=resolved_max_tokens,
        keep_rounds=resolved_keep_rounds,
    )

# 包装 runner：上下文注入对所有 Runner 生效
context_builder = RuntimeContextBuilder(
    memory_manager=memory_manager,
    skills_registry=skills_registry,
)
wrapped_runner = ContextBuildingAgentRunner(runner, context_builder)

self.gateway = RuntimeGateway(
    session_store=self._session_store,
    agent_runner=wrapped_runner,      # Gateway 拿到的是 wrapped runner
    delivery=self.delivery,
    compressor=compressor,            # compressor 用原始 runner 创建，不受 wrapper 影响
    memory_manager=memory_manager,    # Gateway 保留，用于 daily memory 生命周期
    # skills_registry 已移到 context_builder，不再传给 Gateway
)
```

> 顺序：原始 runner → 用原始 runner 创建 compressor → 包 wrapper → Gateway 拿 wrapped runner。
> compressor 内部直接持有 runner.client 和 runner.model 的引用，不受 wrapper 影响。

### 3.7 修改 `web/backend/app.py` 的 `_build_default_gateway()`

同 3.6 的顺序逻辑：
1. 创建 `DeepSeekAgentRunner`
2. 创建 `RuntimeContextBuilder`
3. 包 `ContextBuildingAgentRunner`
4. 传 `RuntimeGateway(agent_runner=wrapped_runner)`

### 3.8 更新测试

**`tests/test_web_channel.py`（旧文件）**:
- 删除旧文件（已被 Phase 1 的新 `tests/test_web_channel.py` 替代）

**`tests/test_gateway.py`**:
- `test_gateway_injects_memory_context_before_runner` → 迁移到 `test_runtime_context_builder.py`
- Gateway 的 daily memory 测试保留

**`tests/test_skills_integration.py`**:
- `TestGatewaySkillInjection` → 迁移到 `test_runtime_context_builder.py`
- `test_load_skill_instructions_not_in_system_prompt` → 改为直接调用 `RuntimeContextBuilder.build()`

**`tests/test_web_api.py`**:
- 确认 HTTP 端到端流程通过（Router 已改走 Processor 管线 + RAISE 策略）

**`tests/test_processor.py`**（如存在）:
- 补充 ErrorPolicy.SWALLOW 和 ErrorPolicy.RAISE 测试

### 验证
- `grep -r "from web.backend.channel" claw/ web/ tests/` 应无结果
- 全量测试通过

---

## 涉及文件汇总

| 操作 | 文件 | Phase |
|------|------|-------|
| 修改 | `claw/ports.py` (添加 ContextBuilder + 补充 Gateway.handle_stream) | 1 |
| 修改 | `claw/processor.py` (增加 ErrorPolicy) | 1 |
| 新增 | `claw/channels/web/__init__.py` | 1 |
| 新增 | `claw/channels/web/transport.py` | 1 |
| 新增 | `claw/channels/web/adapter.py` | 1 |
| 新增 | `claw/channels/web/delivery.py` | 1 |
| 修改 | `web/backend/schemas/chat.py` (添加 client_event_id) | 1 |
| 新增 | `tests/test_web_channel.py`（替代旧文件） | 1 |
| 新增 | `claw/agent_runtime/context.py` | 2 |
| 新增 | `claw/agent_runtime/wrapper.py` | 2 |
| 新增 | `tests/test_runtime_context_builder.py` | 2 |
| 修改 | `web/backend/app.py`（统一注入 Processor + Transport） | 3 |
| 修改 | `web/backend/routers/chat.py`（走 Transport → Processor 管线） | 3 |
| 修改 | `web/backend/routers/conversations.py`（用 WebAdapter.make_message） | 3 |
| 删除 | `web/backend/channel.py` | 3 |
| 修改 | `claw/gateway.py`（删除 inject 方法，瘦身） | 3 |
| 修改 | `claw/agent.py`（接入 wrapper，注意顺序） | 3 |
| 修改 | `tests/test_gateway.py`（迁移 memory 注入测试） | 3 |
| 修改 | `tests/test_skills_integration.py`（迁移 skill 注入测试） | 3 |

## 关键设计决策

1. **Web 完整四件套（Transport + Adapter + Processor + Delivery）** — 与 Local Channel 同级，接新平台只需写差异层。
2. **ChannelProcessor error policy** — SWALLOW（webhook 场景）vs RAISE（Web SSE 场景），Web 用 RAISE 让 Router 能 encode_error 发给前端。
3. **web_processor 在 _gateway 确定后统一创建** — 无论走默认构建还是外部注入，都不会遗漏。
4. **wrapper 接入顺序：原始 runner 创建 compressor → 再包 wrapper** — 避免 isinstance 检查失效导致自动压缩静默中断。
5. **Gateway Protocol 补充 handle_stream** — 与实际使用对齐，processor.py 已经在调用。
6. **WebTransport 支持 client_event_id** — 客户端传入时用于去重（重试/双击），无值时 fallback uuid。
7. **WebAdapter 从 payload 读 account/peer/sender** — payload 优先 + 常量 fallback，为多用户预留入口。
8. **ContextBuildingAgentRunner wrapper** — 任何 Runner 都能获得上下文注入，不硬编码在 DeepSeek 里。
9. **skip_runtime_context 标记** — `_full_compact` 内部调用 runner 时不注入 memory/skills。
10. **make_message 委托 to_inbound_message** — 走标准 Adapter 流程，不是换皮裸函数。

## Review 发现与处理

| # | 严重度 | 发现 | 处理 |
|---|--------|------|------|
| 1 | High | context_builder 只接到 DeepSeek 会让自定义 Runner 拿不到 context | 改用 ContextBuildingAgentRunner wrapper |
| 2 | High | `_full_compact()` 内部调 runner 会被 context 污染 | 加 `skip_runtime_context` 标记 |
| 3 | Medium | `make_message` 绕过 Adapter 协议只是换皮 | 构造 PlatformEvent 委托 `to_inbound_message` |
| 4 | Medium | 漏了 `test_skills_integration.py` 的测试迁移 | Phase 3 一并迁移 |
| 5 | Low | ContextBuilder Protocol 应提前到 Phase 4 前 | 移到 Phase 1 |
| 6 | Low | SseEncoder error path 未迁移 | Phase 1 中包含 encode_error |
| 7 | High | 缺少 WebTransport 设计 | 新增 WebTransport，Router 走完整管线 |
| 8 | High | Processor 吞 SSE 错误 + Processor 注入时机 + wrapper 顺序搞坏压缩 | 增加 ErrorPolicy.RAISE；_gateway 确定后统一创建；原始 runner 创建 compressor 后再包 wrapper |
| 9 | Medium | 随机 event_id 去重无效 | ChatStreamRequest 支持 client_event_id，WebTransport.receive 传入 |
| 10 | Medium | WebAdapter 写死常量堵住多用户 | payload 优先 + 常量 fallback，与 LocalAdapter 一致 |

## 端到端验证

```powershell
# 全量测试
uv run pytest tests/ -v

# 单独验证 Web Channel 完整管线
uv run pytest tests/test_web_channel.py tests/test_web_api.py tests/test_integration_flow.py -v

# 验证 Gateway 瘦身后功能
uv run pytest tests/test_gateway.py tests/test_runtime_context_builder.py -v

# 验证技能集成
uv run pytest tests/test_skills_integration.py -v
```
