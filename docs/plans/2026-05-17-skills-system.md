# 两层技能加载机制：Tool 驱动的按需加载

## Context

当前 Skills 系统（刚完成重构）有两层注入：
1. `skills_listing`：所有技能的 name+description 列表，**始终注入系统提示词**
2. `skill_instructions`：激活技能的完整指令，注入系统提示词

问题：
- `skills_listing` 虽然只有 name+description，但仍是系统提示词的一部分，每轮都占用 context
- `skill_instructions` 也占系统提示词空间
- LLM 的 `[ACTIVATE: skill-name]` 标记机制需要解析回复文本，不够可靠

用户要求的方案：**通过 tool_result 注入完整技能内容，不塞系统提示词**。

```
Layer 1（系统提示 - 轻量）:
    Skills available:
      - pdf: Process PDF files...        ~100 tokens/技能
      - code-review: Review code...

Layer 2（按需加载 - tool_result 注入）:
    当模型调用 load_skill("pdf") 时，tool_result 返回完整指令
```

---

## 变更设计

### 核心思路

1. **删除** `[ACTIVATE: ...]` 标记机制（`parse_activation`、`strip_activation_marker`、`_process_skill_activation`）
2. **删除** `skill_instructions` 系统提示词注入（不再通过 system message 注入完整指令）
3. **新增** `load_skill` 内置工具：LLM 调用 `load_skill("pdf")` → 返回完整技能指令作为 tool_result
4. `skills_listing` 保留为系统提示词注入（轻量级索引，~100 tokens/技能）
5. 技能指令通过 tool_result 注入后，LLM 自然看到完整指令并按其工作

### 数据流

```
用户输入 → Gateway._inject_skill_context()
  → 注入 skills_listing（轻量索引）到 session.metadata["skills_listing"]
  → 不再注入 skill_instructions

LLM 看到技能列表 → 决定需要 pdf 技能 → 调用 load_skill("pdf")
  → tool_result: <skill name="pdf">完整 PDF 处理指令</skill>
  → LLM 按指令执行后续步骤
```

### 优势

- **Token 节省**：10 个技能 × 1500 tokens = 15000 tokens（单层），两层加载只需 ~1000 tokens（索引）+ 1500 tokens（1 个技能）= 2500 tokens
- **可靠性**：用标准 tool call 机制替代文本标记解析，不依赖 LLM 输出格式
- **无额外 API 调用**：tool_result 在同一轮对话中返回，不增加请求次数

---

## 模块变更清单

### 1. 新增 `claw/builtin_tools/skill_loader.py` — `load_skill` 工具

```python
async def _load_skill(args: dict[str, Any]) -> str:
    """加载技能的完整指令。

    Args:
        args: {"name": "skill-name"}

    Returns:
        格式化的技能指令，作为 tool_result 注入对话
    """
    name = args.get("name", "")
    skill = _skills_registry.get(name)
    if skill is None:
        return f"Error: skill '{name}' not found. Use list_skills to see available skills."
    # 返回完整指令
    parts = [f'<skill name="{skill.name}">', skill.instructions]
    if skill.tools:
        parts.append(f"Available tools: {', '.join(skill.tools)}")
    parts.append("</skill>")
    return "\n".join(parts)

def register(registry: ToolsRegistry, skills_registry: SkillsRegistry) -> None:
    """注册 load_skill 工具，需要 skills_registry 引用。"""
    global _skills_registry
    _skills_registry = skills_registry
    registry.register(Tool(
        name="load_skill",
        description="Load a skill's full instructions by name. Use this when you need to follow a specific skill's workflow.",
        handler=_load_skill,
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The skill name to load (e.g. 'code-review', 'translate')",
                },
            },
            "required": ["name"],
        },
    ))
```

### 2. 修改 `claw/builtin_tools/__init__.py`

在 `register_all()` 中新增 `skill_loader` 的注册：

```python
def register_all(registry: ToolsRegistry, *, skills_registry: Any = None) -> None:
    ...
    if skills_registry is not None:
        from claw.builtin_tools.skill_loader import register as register_skill_loader
        register_skill_loader(registry, skills_registry)
```

`register_all` 需要新增可选参数 `skills_registry`。

### 3. 修改 `claw/skills/registry.py`

- **删除** `parse_activation()` 静态方法
- **删除** `strip_activation_marker()` 静态方法
- **删除** `build_skill_prompt()` — 不再需要，完整指令由 load_skill 工具返回
- **保留** `build_skills_listing()` — 轻量级索引
- **保留** `activate()` / `deactivate()` / `active_skill` — 仍可用于斜杠命令手动激活场景
- **新增** `get_skill_instructions(name)` 方法 — 供 load_skill 工具调用，返回格式化的完整指令

### 4. 修改 `claw/gateway.py`

- **简化** `_inject_skill_context()`：
  - 只注入 `skills_listing`（轻量索引）
  - **删除** `skill_instructions` 注入逻辑
  - **删除** 手动激活技能时注入完整指令的逻辑
- **删除** `_process_skill_activation()` 方法
- **删除** `handle_inbound_message` 和 `handle_stream` 中对 `_process_skill_activation` 的调用
- 斜杠命令激活技能后，LLM 在下一轮对话中看到 `skills_listing` 中的标记（`*` active），可通过 `load_skill` 再次加载

### 5. 修改 `claw/deepseek.py`

- **删除** `skill_instructions` 注入（`_build_messages` 中的相关代码）
- **保留** `skills_listing` 注入
- 注入顺序变为：summary → memory_context → skills_listing → history

### 6. 修改 `claw/agent.py`

- **删除** `skill_matcher` 相关代码（已在上一轮删除，确认无残留）
- `start()` 中调用 `register_all` 时传入 `skills_registry`

### 7. 修改 `chat/app.py`

- `_make_claw()` 中调用 `register_all(registry, skills_registry=skills)` 注册 load_skill 工具
- 斜杠命令激活技能后，提示用户 LLM 将在下一轮自动加载（不再注入完整指令）

### 8. 修改 `claw/skills/__init__.py`

- 更新导出（删除已移除的方法引用）

---

## 测试变更

### 修改 `tests/test_skill_registry.py`
- 删除 `parse_activation` 和 `strip_activation_marker` 相关测试
- 删除 `build_skill_prompt` 测试（如果不再使用）
- 新增 `get_skill_instructions()` 测试

### 修改 `tests/test_skills_integration.py`
- 删除 `[ACTIVATE: ...]` 标记解析测试
- 新增 `load_skill` 工具调用测试：
  - 调用 `load_skill("translate")` 返回完整指令
  - 调用 `load_skill("nonexistent")` 返回错误信息
  - 技能指令通过 tool_result 注入，不出现在系统提示词中
- 修改注入顺序测试：summary → memory_context → skills_listing

---

## 关键文件清单

| 文件 | 操作 |
|------|------|
| `claw/builtin_tools/skill_loader.py` | **新增** — load_skill 工具 |
| `claw/builtin_tools/__init__.py` | 修改 — register_all 新增 skills_registry 参数 |
| `claw/skills/registry.py` | 修改 — 删除 parse/strip_activation，新增 get_skill_instructions |
| `claw/gateway.py` | 修改 — 简化为只注入 listing，删除 process_skill_activation |
| `claw/deepseek.py` | 修改 — 删除 skill_instructions 注入 |
| `claw/agent.py` | 修改 — register_all 传入 skills_registry |
| `chat/app.py` | 修改 — register_all 传入 skills_registry |
| `claw/skills/__init__.py` | 修改 — 更新导出 |
| `tests/test_skill_registry.py` | 修改 — 更新测试 |
| `tests/test_skills_integration.py` | 修改 — 更新测试 |
| `docs/plans/2026-05-17-skills-system.md` | 更新 — 同步本方案 |

---

## 验证方式

1. **单元测试**：全量运行 `uv run pytest`
2. **手动验证**：
   - `uv run mini-claw-chat` → 输入 "帮我翻译这段话" → LLM 调用 `load_skill("translate")` → 返回完整翻译指令 → LLM 按指令翻译
   - `/code-review` → 斜杠命令激活 → LLM 在下一轮调用 `load_skill("code-review")` 加载完整指令
3. **回归验证**：确保现有非 skills 测试全部通过
4. **代码审查**：实现完成后调用 codex:adversarial-review

---

## Review 修复记录（2026-05-18）

代码审查发现 5 个问题，已全部修复：

| 问题 | 级别 | 修复方式 |
|------|------|----------|
| `store.delete()` 路径穿越 | Critical | 添加 `is_valid_name()` 校验 + resolved path containment check |
| bundled skills 在 gitignored `data/` 中 | High | 迁移到 `claw/skills/bundled/`（随包分发），`SkillStore` 用 `Path(__file__)` 定位 |
| `/skills remove` 可删除 bundled 技能 | High | `delete()` 仅允许删除 `local/` 目录，保护 bundled 技能 |
| export 前有 save 副作用 | Medium | `export_skill`/`export_skills` 直接从 Skill 对象生成内容，不写回 store |
| 斜杠命令激活后模型缺少上下文 | Medium | `build_skills_listing()` 有活跃技能时明确提示 LLM 调用 `load_skill` |

### 构造函数变更

- `SkillStore.__init__` 新增 `bundled_dir` 参数，默认为包内目录，测试可覆盖
- 测试 fixture 统一传入 `bundled_dir=tmp_path / "skills" / "bundled"` 避免写包目录

### 新增测试

- `test_delete_rejects_invalid_name` — 路径注入名称被拒绝
- `test_delete_only_removes_local` — bundled 技能不可被删除
- `test_build_skills_listing_marks_active` — 增加 `Call load_skill` 断言
