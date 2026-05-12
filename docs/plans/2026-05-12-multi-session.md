# Multi-Session 支持 & JSONL 持久化

## Context

当前 session_key (`channel:account_id:peer_id`) 一对一映射到一个 Session，同一用户无法开多个独立对话。
本次重构引入两层结构：peer_key（用户分组）+ session_id（对话标识），支持多会话管理、JSONL 持久化、手动上下文压缩。

## 设计概览

```
peer_key: "local:bot001:user_shaw"
  ├── sess_a1b2c3  (active)
  ├── sess_d4e5f6
  └── sess_g7h8i9

存储结构:
data/sessions/
  ├── index.json                # peer_key → {active, sessions: {id: meta}}
  ├── sess_a1b2c3.jsonl         # 每行一条 ChatMessage
  └── sess_d4e5f6.jsonl
```

## 实现步骤

### Phase 1: 数据结构 & Protocol（向后兼容）

**1.1 `claw/types.py`**
- `ChatMessage` 增加可选字段 `ts: int | None = None`
- `Session` 增加字段 `summary: str | None = None`（compact 后的摘要）

**1.2 `claw/ports.py`**
- `SessionStore` Protocol 扩展：
  ```python
  class SessionStore(Protocol):
      async def get(self, session_key: str) -> Session | None: ...
      async def save(self, session: Session) -> None: ...
      async def get_by_id(self, session_id: str) -> Session | None: ...
      async def delete(self, session_id: str) -> None: ...
      async def list_sessions(self, peer_key: str) -> list[Session]: ...
      async def get_active(self, peer_key: str) -> Session | None: ...
      async def set_active(self, peer_key: str, session_id: str) -> None: ...
  ```

**1.3 `claw/session.py` — InMemorySessionStore**
- 内部存储改为 `_sessions: dict[str, Session]`（session_id → Session）
- 增加 `_active: dict[str, str]`（peer_key → active session_id）
- `get(session_key)` 改为返回 active session（向后兼容）
- 实现 `get_by_id`, `delete`, `list_sessions`, `get_active`, `set_active`
- `delete` 时若删的是 active，自动切到同 peer 下的另一个 session

**1.4 `tests/test_ports_contracts.py`**
- `FakeSessionStore` 实现新增的所有方法

**1.5 更新 `tests/test_session.py`**
- 测试 InMemorySessionStore 的新方法：get_by_id, delete, list_sessions, get_active, set_active
- 测试 delete active session 后自动切换
- 测试 get(session_key) 返回 active session

### Phase 2: Gateway 适配

**2.1 `claw/gateway.py`**
- `handle_inbound_message` 和 `handle_stream`：session 查找改为 `get_active(peer_key)`
- 新增方法：
  - `create_new_session(message)` → 创建新 session 并 activate
  - `list_sessions(message)` → 列出 peer 下所有 session
  - `select_session(message, session_id)` → 切换 active
  - `delete_session(message, session_id)` → 删除 session
  - `compact_session(message)` → 压缩当前 session 上下文

**2.2 更新 `tests/test_gateway.py`**
- 更新已有测试用例适配新的 session 查找逻辑
- 新增测试：create_new_session, select_session, delete_session, compact_session
- 边界：delete active session 后行为、compact 空 history

### Phase 3: AgentRunner 支持 summary

**3.1 `claw/deepseek.py`**
- 提取 `_build_messages(session)` 方法
- 当 `session.summary` 非空时，在 messages 开头插入 system message：
  `{"role": "system", "content": f"以下是之前对话的摘要：\n{session.summary}"}`
- `run()` 和 `run_stream()` 都使用 `_build_messages`

**3.2 `claw/runner.py`**
- `EchoAgentRunner` 同步添加 `_build_messages`，对 summary 透传（测试用，不影响 echo 行为）

**3.3 更新 `tests/test_deepseek.py`**
- 新增测试：session 有 summary 时 messages 包含 system 摘要

### Phase 4: JsonlSessionStore

**4.1 `claw/session.py` — 新增 JsonlSessionStore**

存储结构：
```
data/sessions/index.json:
{
  "local:local-app:local-user": {
    "active": "sess_abc123",
    "sessions": {
      "sess_abc123": {"channel": "local", "account_id": "local-app", ...},
      "sess_def456": {"channel": "local", "account_id": "local-app", ...}
    }
  }
}

data/sessions/sess_abc123.jsonl:
{"role": "user", "content": "hello", "ts": 1747...}
{"role": "assistant", "content": "hi!", "ts": 1747...}
```

核心逻辑：
- `save(session)`: 追加新消息到 JSONL（通过 `_saved_count` 追踪已持久化条数），更新 index.json
- `get_by_id(session_id)`: 读取 JSONL 全部行重建 history，从 index 读元数据，组装 Session
- `get(session_key)`: 从 index 读 active session_id，再 get_by_id
- `delete(session_id)`: 删除 JSONL 文件，更新 index
- `list_sessions(peer_key)`: 从 index 读取列表，不加载 history（用 index 中的元数据构建轻量 Session）
- compact 后：index 中更新 summary，JSONL 文件不动

进程重启处理：加载 session 时 `_saved_count[session_id] = len(history)`，避免重复追加。

**4.2 新增 `tests/test_jsonl_session_store.py`**
- 使用 `tmp_path` fixture
- 测试 save → get_by_id → history 完整
- 测试多次 save 追加行为
- 测试 delete 文件和 index 更新
- 测试 list_sessions
- 测试 get/set active
- 测试进程重启模拟（重新创建 JsonlSessionStore 实例后能加载）

### Phase 5: MiniClaw 门面

**5.1 `claw/agent.py`**
- `__init__` 增加 `session_store` 可选参数，默认 `JsonlSessionStore()`
- 增加 `_routing_message()` 辅助方法（复用 transport + adapter 获取 peer 信息）
- 增加异步方法：
  - `new_session() → Session`
  - `list_sessions() → list[Session]`
  - `select_session(session_id) → Session | None`
  - `delete_session(session_id) → None`
  - `compact_session() → None`
  - `get_active_session_id() → str | None`
- 默认 delivery 改为 `LocalDelivery()`（JsonlSessionStore 已接管持久化）

**5.2 更新 `tests/test_agent.py`**
- 测试使用 InMemorySessionStore 替代默认 store
- 测试 new_session, list_sessions, select_session 等方法

### Phase 6: Compact 逻辑

**6.1 `claw/session.py` — generate_summary()**
- 接收 `history: list[ChatMessage]` 和 `runner: AgentRunner` 和 `session: Session`
- 构建摘要 prompt："请总结以下对话的关键信息：\n{history_text}"
- 创建临时 session 和 message 调用 runner，返回摘要文本

**6.2 `claw/gateway.py` — compact_session()**
- 获取 active session
- 调用 generate_summary 得到摘要
- 设置 `session.summary`（追加式：如果已有 summary，新 prompt 包含旧 summary）
- 清空 `session.history`
- save

**6.3 测试**
- 测试 compact_session 后 history 清空、summary 非空
- 测试 compact 空 history 是 no-op

### Phase 7: CLI 命令

**7.1 `chat/app.py`**
- 在消息循环中拦截命令（在调用 MiniClaw 之前）：
  - `/new` → 创建新 session，打印 session_id
  - `/sessions` → 列出所有 session，active 的标 `*`
  - `/select [id]` → 切换 session
  - `/delete [id]` → 删除 session
  - `/compact` → 压缩当前 session，打印摘要和 token 节省信息
- 提示文字更新，列出可用命令

**7.2 更新 `tests/test_chat_app.py`**
- 测试各命令的输入输出行为

### Phase 8: 文档 & 清理

- 更新 CLAUDE.md 架构说明
- 保存此 plan 到 docs/plans/

## 涉及文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `claw/types.py` | 修改 | ChatMessage 加 ts, Session 加 summary |
| `claw/ports.py` | 修改 | SessionStore Protocol 扩展 |
| `claw/session.py` | 修改 | InMemorySessionStore 更新, 新增 JsonlSessionStore, generate_summary |
| `claw/gateway.py` | 修改 | session 查找逻辑, 新增 session 管理方法 |
| `claw/agent.py` | 修改 | session_store 参数, session 管理便捷方法 |
| `claw/deepseek.py` | 修改 | _build_messages 支持 summary |
| `claw/runner.py` | 修改 | _build_messages 支持 summary |
| `chat/app.py` | 修改 | 命令解析 |
| `tests/test_session.py` | 修改 | 新方法测试 |
| `tests/test_gateway.py` | 修改 | 新逻辑测试 |
| `tests/test_agent.py` | 修改 | 新接口测试 |
| `tests/test_deepseek.py` | 修改 | summary 上下文测试 |
| `tests/test_chat_app.py` | 修改 | 命令测试 |
| `tests/test_ports_contracts.py` | 修改 | FakeSessionStore 扩展 |
| `tests/test_jsonl_session_store.py` | **新建** | JsonlSessionStore 完整测试 |

## 验证方式

1. `uv run pytest` — 全量测试通过
2. `uv run mini-claw-chat` — 手动测试：
   - 发几条消息，`/new` 开新对话，`/sessions` 列出，`/select` 切换
   - `/compact` 压缩后继续对话，验证上下文保持
   - 退出重启后，`/sessions` 仍能看到之前的会话
   - `/delete` 删除后验证 JSONL 文件和 index.json 更新
