# Auto Context Compression Plan

## Context

### 问题背景

当前压缩有两个核心问题：

**问题 A — JSONL 重载 bug（已确认）：**
`/compact` 执行流程：`CLI → MiniClaw.compact_session() → Gateway.compact_session() → 设置 session.summary + 清空 session.history → save()`

在 `JsonlSessionStore` 下：
- `save()` 只追加 `session.history[saved:]`，不截断旧 JSONL（设计如此，保留原始记录）
- compact 后 `session.history = []`，但 `_saved_count` 被重置为 0
- 下次 `get_active()` 调用 `_read_history()` 会读回 JSONL 全部记录，旧消息"复活"
- 结果：LLM 实际收到的是 `system(summary) + 旧历史 + 新消息`，而非设计意图的 `system(summary) + 新消息`

`InMemorySessionStore` 下测试能通过（内存对象确实被清空了），但持久化场景不生效。

**问题 B — 纯手动触发：**
压缩只能通过 `/compact` 命令触发，没有自动机制。

**目标：** 实现基于 Token 阈值的自动压缩，保留最近 N 轮对话，同时修复 JSONL 重载 bug。

## Phase 1: Token 估算 — `claw/tokens.py`（新文件）

零依赖字符估算：
- 拉丁文：~4 字符/token
- CJK（中日韩）：~1.5 字符/token
- 混合内容分别计数

```python
def estimate_tokens(text: str) -> int
def estimate_session_tokens(session: Session) -> int  # summary + history
```

## Phase 2: 修复 JSONL 重载 — `claw/session.py` + `claw/types.py`

**根因：** `_read_history()` 不感知哪些消息已被压缩，每次都从 JSONL 读全部记录。

**修复：** `Session` dataclass 新增 `history_offset: int = 0` 显式字段（review 意见：不用 metadata dict，类型安全且不会被业务 metadata 覆盖）。index.json 的 session 元数据同步增加 `history_offset` 字段。

### `claw/types.py` 改动：
- `Session` dataclass 新增 `history_offset: int = 0`，位于 `summary` 之后

### `claw/session.py` 改动（`JsonlSessionStore`）：
- `_session_meta()` — 从 `session.history_offset` 写入 index
- `_meta_to_session()` — 从 index 读取 `history_offset` 赋值给 Session 字段
- `_read_history(session_id, offset=0)` — 跳过前 offset 条有效记录
- `get_active()` / `get_by_id()` — 传入 `session.history_offset` 读 history，正确计算 `_saved_count`
- `save()` — 防御性 clamping（review 意见：处理 `_saved_count` 缺失/stale）

**`save()` 核心逻辑（含防御）：**
```python
offset = session.history_offset
saved_total = max(self._saved_count.get(session_id, offset), offset)
already_persisted = max(0, min(len(session.history), saved_total - offset))
new_messages = session.history[already_persisted:]
self._append_messages(session_id, new_messages)
self._saved_count[session_id] = offset + len(session.history)
```

**数据流举例：**
1. 压缩前：JSONL 20 条，offset=0，history=[m0..m19]，saved_count=20
2. 压缩器设置 offset=12，history=[m12..m19]（8条）
3. Runner 追加 user/assistant 后：history=[m12..m19, new1, new2]（10条）
4. save()：saved_total = max(20, 12) = 20，already_persisted = max(0, min(10, 20-12)) = 8，new_messages = history[8:] = [new1, new2]
5. 追写到 JSONL → 22 条，_saved_count = 12+10 = 22
6. index.json 更新 history_offset=12
7. 下次 get_active()：_read_history(offset=12) 只读第12条之后 → [m12..m19, new1, new2]

**向后兼容：** 旧 index.json 无 `history_offset` 字段时 `.get("history_offset", 0)` 默认为 0。旧代码构造 Session 不传 history_offset 时默认 0，行为不变。

## Phase 3: 压缩器 — `claw/compressor.py`（新文件）

```python
class ContextCompressor:
    def __init__(self, *, client: AsyncOpenAI, model: str,
                 max_tokens=8000, keep_rounds=4, enabled=True)

    def should_compress(self, session: Session, incoming_text: str | None = None) -> bool
    async def compress(self, session: Session, *, force: bool = False) -> str | None
```

**关键设计：**
- 直接用 `client.chat.completions.create()` 生成摘要（temperature=0.3, max_tokens=1000），不递归调用 runner
- `_find_split_point()` 从末尾倒数 assistant 消息定位 keep_rounds 边界
- 压缩失败（LLM 报错）时 session 不变（包括 offset），best-effort
- `compress()` 原地修改 `session.summary`、`session.history`、`session.history_offset`
- offset 更新在 LLM 调用成功之后，确保失败时不留脏状态
- 已有 summary 时传入 prompt 让 LLM 合并生成新摘要
- `enabled=False` 只影响自动压缩（`should_compress` 返回 False），不影响手动 `/compact`（`compress(force=True)` 绕过 enabled 和 token 阈值，但仍遵守结构性边界 split_point == 0）
- `should_compress(session, incoming_text)` 估算时包含即将进入的用户消息 token，避免追加后立即超阈值

**compress() 核心流程：**
```
1. split_point = _find_split_point(session)
2. if split_point == 0: return None  # 结构性边界：没有可压缩的内容
3. older = history[:split_point], recent = history[split_point:]
4. new_summary = await _generate_summary(older, existing_summary)  # 调 LLM
5. if new_summary is None: return None  # 失败，session 不变
6. session.history_offset += split_point  # 更新 offset（成功后才更新）
7. session.summary = new_summary
8. session.history = recent
```

**Split invariant（review 意见补充）：**
- 压缩后的 `recent` 必须以 `user` 消息开始，保留最近 N 个完整 user→assistant 回合
- 若 split 落在 assistant 上，向前调整到下一个 user
- 若找不到有效 split（history 全是 user 或不完整），返回 split_point=0，no-op

## Phase 4: Gateway 层编排自动压缩 — `claw/gateway.py` + `claw/deepseek.py` + `claw/agent.py` + `claw/ports.py`

**架构调整（review 意见 #2）：** 自动压缩的编排移到 Gateway 层，确保压缩后立即持久化，再调 runner。Runner 不再持有 compressor。

### `claw/ports.py`：
- 添加 `ContextCompressor` Protocol（`should_compress` + `compress`）
- `AgentRunner` Protocol 补充 `run_stream()` 方法

### `claw/gateway.py`：
- 构造函数新增 `compressor: ContextCompressor | None = None`（直接注入，不需要 SupportsCompression Protocol 或 isinstance）
- `handle_inbound_message()` 流程：获取 session → **估算上下文（含即将进入的用户消息）→ 压缩 → 压缩成功则立即 save()** → 调 runner → save()
- `handle_stream()` 同理
- `compact_session()` 使用 compressor + `force=True`，无 compressor 时 fallback 到原逻辑
- fallback 路径也要 `session.history_offset += len(session.history)` 再 `session.history = []`
- **两条路径语义不同（测试需明确区分）：** compressor 路径保留最近 N 轮（`history` 非空），fallback 路径全量清空（`history = []`）

### `claw/deepseek.py`：
- **不再持有 compressor**，回归纯 runner 职责
- `run()` 和 `run_stream()` 不变

### `claw/agent.py`（MiniClaw）：
- 新增 `auto_compact`/`max_tokens`/`keep_rounds` 构造参数
- 创建 ContextCompressor 并注入到 RuntimeGateway（而非 DeepSeekAgentRunner）

**handle_inbound_message 新流程：**
```
1. session = get_or_create_session()
2. if compressor and compressor.should_compress(session, incoming_text=message.text):
3.     summary = await compressor.compress(session)
4.     if summary is not None:
5.         await session_store.save(session)   ← 仅压缩成功时立即持久化
6. reply = await agent_runner.run(session, message)
7. await session_store.save(session)          ← 正常保存
8. await delivery.send(message, reply)
```

## Phase 5: 测试

| 文件 | 测试内容 |
|------|---------|
| `tests/test_tokens.py`（新） | CJK/拉丁/混合 token 估算、空字符串、最少返回 1 |
| `tests/test_compressor.py`（新） | should_compress 阈值判断、compress 分割逻辑、force 参数、已有 summary 合并、LLM 失败容错 |
| `tests/test_jsonl_session_store.py`（改） | history_offset 压缩后重载不复活旧消息、多次压缩 offset 累积、向后兼容、`_saved_count` missing/stale 场景 |
| `tests/test_gateway.py`（改） | 自动压缩触发并立即 save、compact_session 使用 compressor+force、fallback 路径 |

关键边界测试：
- 历史不足 `keep_rounds*2+2` 时不压缩
- 只有 user 无 assistant 时不压缩（无法形成有效摘要）
- split 点落在孤立 assistant 时向前调整（recent 必须从 user 开始）
- 多次压缩 offset 累积正确
- 压缩后追加新消息不重复
- `_saved_count` 未初始化时 save 不重复追写旧消息（review #1）
- 流式中断时已压缩的 session 已被持久化（review #2）
- `/compact` 手动触发时 force=True 绕过阈值但遵守结构性边界（review #3）

## 验证方式

```bash
uv run pytest tests/ -v
```

全部测试通过后，手动测试：
1. `uv run mini-claw-chat` 启动
2. 连续对话多轮，观察是否自动压缩（可通过日志确认）
3. `/compact` 手动压缩仍正常工作
4. 重启 chat 后加载 session，验证 history 不包含已压缩的消息
