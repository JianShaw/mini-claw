# Plan: Skills Web API + Frontend

## Context

项目的 Skills 后端（registry / store / loader / marketplace）已经完整实现，但缺少 Web API 路由和前端 UI。
用户无法通过 Web 界面安装、管理技能。需要参照已有的 Expert 系统（router + schema + deps + frontend）补齐这一层。

Claude Code 的插件机制参考：
- 支持 marketplace 发现 + GitHub 仓库 + 本地文件 + ZIP 安装
- SKILL.md 格式（YAML frontmatter + Markdown body）
- 存储在 ~/.claude/skills/ 或 .claude/skills/ 目录
- 两层加载：轻量索引在 system prompt + 完整指令通过 tool call 按需加载

## Implementation Steps

### Step 1: `web/backend/schemas/skill.py` (新建)

Pydantic 模型，参照 `web/backend/schemas/expert.py`：

- `SkillMetaSchema` — version, author, tags, category
- `SkillListItemSchema` — 轻量列表（name, description, source, version, tools, category），不含 instructions
- `SkillSchema` — 完整详情（含 instructions），带 `from_skill()` classmethod
- `ExportRequestSchema` — `{ names: list[str] }`

### Step 2: `web/backend/routers/skills.py` (新建)

REST API 路由，参照 `web/backend/routers/experts.py`：

| Method | Path | 功能 |
|--------|------|------|
| GET | /skills | 列出所有技能（?q= 搜索） |
| GET | /skills/{name} | 获取技能详情（含 instructions） |
| POST | /skills/install/file | 从 SKILL.md 上传安装（multipart） |
| POST | /skills/install/zip | 从 ZIP 上传安装（multipart） |
| DELETE | /skills/{name} | 卸载技能（仅 local，bundled 受保护） |
| GET | /skills/{name}/export | 导出单个技能 SKILL.md |
| POST | /skills/export | 批量导出为 ZIP |

关键实现细节：
- 文件上传用 `UploadFile` + `NamedTemporaryFile`，finally 块清理临时文件
- 导出 ZIP 用 `FileResponse` + `BackgroundTasks` 清理
- bundled 技能不允许删除（`MarketplaceOps.remove` 返回 False → 400）

### Step 3: `web/backend/deps.py` (修改)

新增三个 DI 工厂函数，参照已有的 expert 模式：

```python
def get_skill_store() -> SkillStore
def get_skill_registry() -> SkillsRegistry
def get_marketplace_ops() -> MarketplaceOps
```

### Step 4: `web/backend/app.py` (修改)

1. `create_app` 中构建共享的 `SkillStore`、`SkillsRegistry`、`MarketplaceOps`
2. 将共享 `SkillsRegistry` 传入 `_build_default_gateway`（取代内部创建）
3. `_wire_deps` 签名扩展，注册三个新的 `dependency_overrides`
4. 注册 `skills.router`

**关键约束**：Gateway 和 Marketplace 必须共享同一个 `SkillsRegistry` 实例，否则 Web 端安装的技能在聊天中不可见。

### Step 5: `tests/test_web_skill_api.py` (新建)

集成测试，参照 `tests/test_web_api.py`：

- test_list_skills — 列出技能
- test_list_skills_with_search — 搜索过滤
- test_get_skill — 获取详情
- test_get_skill_not_found — 404
- test_install_from_file — 上传 SKILL.md 安装
- test_install_from_zip — 上传 ZIP 安装
- test_uninstall_skill — 卸载 local 技能
- test_uninstall_bundled_forbidden — bundled 不可删除
- test_export_skill — 导出单个技能
- test_export_skills — 批量导出 ZIP

用 `tmp_path` 隔离文件存储，通过 `dependency_overrides` 注入测试实例。

### Step 6: `web/frontend/src/api/client.ts` (修改)

新增 Skill 类型定义和 API 函数：

```typescript
interface SkillMeta { version, author, tags, category }
interface SkillListItem { name, description, source, version, tools, category }
interface Skill { name, description, instructions, tools, meta, source, path }

fetchSkills(q?), fetchSkill(name), installSkillFromFile(file),
installSkillFromZip(file), uninstallSkill(name),
exportSkill(name), exportSkills(names)
```

文件上传用 `FormData`，导出用 `Blob` 响应。

### Step 7: `web/frontend/src/components/SkillMarketplace.tsx` (新建)

参照 `ExpertMarketplace.tsx` 的卡片布局：

- 顶部：搜索框 + 上传按钮（SKILL.md / ZIP）
- 主体：技能卡片网格
  - 每张卡片：名称、描述、来源标记（bundled/local）、标签、版本
  - bundled 技能显示 "内置" 标记，不显示卸载按钮
  - local 技能显示卸载和导出按钮
- 点击卡片展开显示完整 instructions

### Step 8: `web/frontend/src/App.tsx` (修改)

- 导航栏新增 "技能市场" 链接
- 路由新增 `/skills` → `SkillMarketplace`

### Step 9: `docs/plans/2026-05-21-skills-web-api.md` (新建)

保存设计文档到 docs 目录。

## Verification

1. `uv run pytest tests/test_web_skill_api.py -v` — API 测试通过
2. `uv run pytest tests/ -v` — 全量测试不回归
3. 启动 Web 服务 `uv run mini-claw-web`，在浏览器中：
   - 访问 /skills 页面，确认能看到 bundled 技能
   - 上传一个 SKILL.md 文件，确认安装成功
   - 卸载 local 技能，确认删除
   - 导出技能，确认文件下载
4. 在聊天中确认通过 Web 安装的技能在对话中可被 load_skill 加载
