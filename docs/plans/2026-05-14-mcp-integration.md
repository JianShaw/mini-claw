# MCP (Model Context Protocol) 集成方案

**日期**: 2026-05-14
**状态**: 已实施

## 概述

将 MCP (Model Context Protocol) 作为外部工具源接入 mini-claw，使 Agent 能透明使用 MCP 服务器提供的工具/资源/提示，与内置工具并存。

核心思路：MCP 工具通过桥接层转化为现有 `Tool` dataclass 注册到 `ToolsRegistry`，`DeepSeekAgentRunner` 无需修改。

## 模块结构

```
claw/mcp/                    # MCP 客户端集成子包
  __init__.py                # 公共 API
  config.py                  # McpConfigLoader：解析 mcp_config.json
  connection.py              # McpServerConnection：单服务器连接生命周期
  manager.py                 # McpManager：编排所有连接
  bridge.py                  # MCP 工具→Tool 桥接（命名空间 + handler 代理）
  types.py                   # MCP 数据类型
mcp_config.json              # 配置文件（项目根目录）
```

## 配置文件格式 (`mcp_config.json`)

```json
{
  "mcpServers": {
    "filesystem": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "mcp_server_filesystem"],
      "env": {"WORKSPACE_ROOT": "/path/to/workspace"}
    },
    "remote-api": {
      "transport": "sse",
      "url": "http://localhost:8080/sse",
      "headers": {}
    },
    "another": {
      "transport": "streamable-http",
      "url": "http://localhost:9000/mcp"
    }
  }
}
```

支持三种传输：`stdio` / `sse` / `streamable-http`。`env` 值支持 `${VAR_NAME}` 环境变量插值。

## 数据流

```
启动：
  mcp_config.json → McpConfigLoader → McpManager
    → McpServerConnection.connect() (stdio/sse/streamable-http)
      → ClientSession.initialize() → discover tools/resources/prompts
    → McpManager.register_tools(registry)
      → bridge: 每个 MCP 工具创建 namespaced Tool (name="server__tool")
      → registry.register() — 与内置工具共存

工具调用（透明）：
  LLM → tool_calls: "filesystem__read_file"
    → registry.execute("filesystem__read_file", args)
      → bridge handler → connection.call_tool("read_file", args)
        → MCP Server → result
```

## 命名空间策略

MCP 工具名加 `{server_name}__{tool_name}` 前缀，避免与内置工具冲突。内置工具保持原名。

## 修改的文件

- `pyproject.toml` — 添加 `mcp` 依赖
- `claw/ports.py` — 添加 `McpProvider` Protocol
- `claw/agent.py` — 添加 `mcp_config_path` 参数 + `start()/stop()/get_mcp_status()` 生命周期
- `chat/app.py` — MCP 生命周期管理（`start()/stop()`）+ `/mcp` 命令

## 测试覆盖

| 文件 | 覆盖内容 |
|------|---------|
| `tests/test_mcp_config.py` | 配置加载：文件不存在→空、stdio/sse/http 解析、env 插值、disabled、格式错误 |
| `tests/test_mcp_connection.py` | 连接生命周期：connect/discover/call_tool/read_resource/get_prompt/disconnect |
| `tests/test_mcp_bridge.py` | 工具桥接：命名空间前缀、handler 委托、参数映射、冲突跳过 |
| `tests/test_mcp_manager.py` | 管理器协调：start/stop、跳过 disabled、失败不阻塞、register_tools、status、reconnect |
| `tests/test_agent.py` | 带/不带 mcp_config_path 均正常 |
| `tests/test_chat_app.py` | `/mcp` 命令 |

## 验证

全量测试通过（282 passed, 2 skipped）。

## 代码审查修复

审查发现并修复了以下问题：

| 风险 | 文件 | 问题 | 修复 |
|------|------|------|------|
| 高 | `gateway.py` | 流式响应将 tool_call/tool_result/system chunk 都拼入 full_text | 只聚合 `chunk.type == "content"` |
| 中 | `shell.py` | 白名单用 `Path(parts[0]).name` 匹配，允许路径遍历绕过 | 拒绝带 `/` 或 `\` 的命令，只允许裸命令名 |
| 中 | `file_ops.py` | `read_text()` 读完再判断大小，大文件直接进内存 | 先 `stat().st_size` 检查，大文件只读前 1MB |
| 中 | `deepseek.py` | tool-call 消息未保存 reasoning_content，thinking 模式恢复后可能报错 | `ChatMessage` 新增 `reasoning_content` 字段，`_build_messages` 恢复时携带 |
| 低 | `deepseek.py` | 流式路径仅靠 finish_reason 判断工具调用，可能丢失 | 改为以 `tool_calls_accum` 为主判断 |
