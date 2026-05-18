# Mini-Claw vs OpenClaw 差距分析

> 日期：2026-05-17
> 状态：初始分析

## 概述

Mini-Claw 已实现 OpenClaw 的**核心架构骨架**（Gateway/Agent 分层、会话管理、记忆系统、工具调用循环、MCP 集成、调度系统），以下是对照 OpenClaw 的主要功能差距。

---

## 已实现的功能

| 模块 | 状态 | 说明 |
|------|------|------|
| Gateway/Agent 分层架构 | ✅ | RuntimeGateway + MiniClaw facade |
| Agent Loop（工具调用循环） | ✅ | 支持 10 次迭代、流式输出 |
| 会话管理 | ✅ | peer_key + session_id 两级、JSONL 持久化 |
| 上下文压缩 | ✅ | Token 阈值检测 + LLM 摘要 + 冷却机制 |
| 记忆系统 | ✅ | 短期（每日）+ 长期 + 混合搜索（向量+BM25） |
| MCP 集成 | ✅ | 多传输（stdio/SSE/HTTP）、工具桥接、命名空间 |
| 工具注册表 | ✅ | 内置 12+ 工具、异步执行、安全限制 |
| 调度系统 | ✅ | Cron/Interval/Event 三种触发器、预定义任务 |
| CLI 通道 | ✅ | LocalTransport + 流式彩色输出 |
| 消息处理管线 | ✅ | 去重、适配、验证、过滤 |

---

## 缺失功能清单

### 1. 多 Agent 隔离（高优先级）

OpenClaw 支持多个独立 Agent，各自有独立工作空间、状态目录、模型配置和权限。

- Agent 间隔离（工作空间、会话存储、工具权限）
- Binding 机制：将通道账号确定性映射到特定 Agent
- 每个 Agent 独立配置模型和工具集
- 独立 agentDir（状态目录）

### 2. 多 IM 通道集成（高优先级）

当前只有 CLI（LocalTransport），OpenClaw 支持 15+ IM 平台。

- 需要实现 Telegram / Discord / Slack / WhatsApp 等 Transport
- 流式消息双模式：
  - **Block Streaming**：消息分块发送，有最小/最大块大小、断行偏好
  - **Preview Streaming**：实时编辑消息，用户看到"正在输入"效果
- 通道认证和配置管理

### 3. 多模型提供商 + 故障转移（中优先级）

当前只有 DeepSeek 一个模型提供商。

- 多模型支持：Claude / GPT / Gemini / Ollama（本地）
- 故障转移链：主模型失败自动切备用模型
- 重试策略、超时、降级规则
- 每个 Agent 可独立配置不同模型

### 4. 安全沙箱（中优先级）

OpenClaw 有 Docker 沙箱隔离 + 工具调用策略 + 操作员权限控制。

- Agent 容器隔离（Docker 或进程级）
- 工具调用策略系统（允许/拒绝规则）
- 操作员范围（operator scope）权限控制
- 密钥管理（Secrets）

### 5. 记忆系统增强（中优先级）

已有短期+长期+向量搜索，但缺少：

- **Dreaming 机制**：后台自动将短期记忆提升为长期记忆，包含 DREAMS.md 层
- **多后端支持**：当前只有 SQLite，OpenClaw 还支持 LanceDB、Honcho（云端）
- 记忆工具：`memory_search`、`memory_get` 的更完整实现

### 6. 插件/技能生命周期钩子（中优先级）

OpenClaw 有完整的 Plugin SDK，在 Agent 生命周期各阶段提供钩子：

- `before_model_resolve` — 模型解析前
- `before_prompt_build` — 提示构建前
- `before_agent_reply` — Agent 回复前
- `before_tool_call` / `after_tool_call` — 工具调用前后
- `message_received` / `message_sending` / `message_sent` — 消息生命周期
- `session_start` / `session_end` — 会话生命周期
- `gateway_start` / `gateway_stop` — 网关生命周期

当前 `ports.py` 有 Protocol 定义，但缺少生命周期钩子系统。

### 7. 会话路由增强（低优先级）

OpenClaw 的会话管理更精细：

- DM 隔离模式（`main` / `per-peer` / `per-channel-peer` / `per-account-channel-peer`）
- 会话自动重置（每日凌晨 + 空闲超时）
- 按来源类型路由（DM / Group / Room / Cron / Webhook）

### 8. 可观测性（低优先级）

OpenClaw 有完整可观测性栈，当前只有基础日志。

- OpenTelemetry 分布式追踪和指标
- Prometheus 指标采集和告警
- 健康检查端点（Health Checks）
- 心跳监测（Heartbeat）
- 会话诊断工具（卡住会话检测）

### 9. HTTP API / WebSocket 接口（低优先级）

OpenClaw 暴露了多种 API 接口，支持远程调用。

- OpenAI 兼容 HTTP API（聊天补全）
- OpenResponses HTTP API
- Tools Invoke HTTP API（直接调用工具）
- WebSocket Gateway Protocol（实时双向通信）

### 10. 多模态支持（低优先级）

OpenClaw 支持语音、图像、视频处理，当前 mini-claw 纯文本。

- 语音：TTS / STT（本地 Whisper + API）
- 图像：摄像头拍照、图像分析
- 视频：帧提取和分析

---

## 建议优先级路线

| 阶段 | 功能 | 理由 |
|------|------|------|
| **P0** | 多模型提供商 + 故障转移 | 最实用，减少对单一模型依赖 |
| **P0** | Telegram/Discord 通道 | 让 Agent 真正可用，不再局限于 CLI |
| **P1** | 多 Agent 隔离 | 支持多用户/多场景 |
| **P1** | 生命周期钩子系统 | 插件化的基础设施 |
| **P2** | 安全沙箱 + 权限策略 | 上线前的必要保障 |
| **P2** | Dreaming 记忆机制 | 提升记忆质量和自动化程度 |
| **P3** | 可观测性 + HTTP API | 运维和远程访问能力 |
| **P3** | 多模态支持 | 扩展交互方式 |

---

## 参考

- OpenClaw GitHub：开源 AI Agent 操作系统，TypeScript 实现，355K stars
- OpenClaw 核心理念：Gateway 负责路由，Agent 负责智能，Model Provider 负责推理
- Mini-Claw 对应架构：RuntimeGateway → MiniClaw → DeepSeekAgentRunner
