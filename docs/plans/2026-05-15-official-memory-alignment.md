# Official Memory Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align mini-claw's memory system with the official OpenClaw Memory concept while preserving the current lightweight Markdown-first implementation.

**Architecture:** Keep memory as local Markdown files and keep `MemoryManager` as the lifecycle coordinator. Add an explicit workspace-style memory layout, daily-note loading for today and yesterday, memory retrieval tools, and an optional Dreaming-style review log on top of the existing daily-to-long-term distillation path.

**Tech Stack:** Python 3.11+, asyncio, stdlib pathlib/json/datetime, existing `ToolsRegistry`, existing `JsonlSessionStore`, pytest.

---

## Reference

Official concept page: `https://docs.openclaw.ai/zh-CN/concepts/memory`

Relevant current files:

- `claw/memory/store.py`
- `claw/memory/manager.py`
- `claw/gateway.py`
- `claw/deepseek.py`
- `claw/agent.py`
- `claw/builtin_tools/__init__.py`
- `tests/test_memory.py`
- `README.md`

## Design Decisions

- Preserve the current default root `data/memory` for compatibility, but make the layout explicitly configurable.
- Support the official-style daily path `memory/YYYY-MM-DD.md` behind a layout option, without silently breaking existing `data/memory/daily/YYYY-MM-DD.md` users.
- Inject long-term memory plus today and yesterday daily notes.
- Add `memory_get` before `memory_search`; exact reads are less risky than search/indexing.
- Implement simple lexical `memory_search` first. Do not introduce embeddings or external index dependencies yet.
- Add Dreaming as a deterministic review log first: candidate score, reason, accepted/skipped, and destination. Defer LLM-based scoring until the deterministic workflow is tested.

## Task 1: Make Memory Layout Explicit

**Files:**
- Modify: `claw/memory/store.py`
- Modify: `claw/memory/manager.py`
- Test: `tests/test_memory.py`

**Step 1: Write failing tests**

Add tests for two layouts:

```python
def test_daily_memory_store_supports_legacy_layout(tmp_path):
    store = DailyMemoryStore(tmp_path, layout="legacy")
    assert store.path_for(date(2026, 5, 15)) == tmp_path / "daily" / "2026-05-15.md"


def test_daily_memory_store_supports_official_layout(tmp_path):
    store = DailyMemoryStore(tmp_path, layout="official")
    assert store.path_for(date(2026, 5, 15)) == tmp_path / "2026-05-15.md"
```

**Step 2: Run the tests**

Run: `uv run pytest tests/test_memory.py -v`

Expected: FAIL because `layout` is not accepted.

**Step 3: Implement minimal layout support**

Add a `layout: Literal["legacy", "official"] = "legacy"` constructor argument to `DailyMemoryStore` and pass it through `MemoryManager`.

For `legacy`, keep `root / "daily" / YYYY-MM-DD.md`.

For `official`, use `root / YYYY-MM-DD.md`.

**Step 4: Run tests**

Run: `uv run pytest tests/test_memory.py -v`

Expected: PASS.

## Task 2: Load Today And Yesterday Daily Notes

**Files:**
- Modify: `claw/memory/manager.py`
- Test: `tests/test_memory.py`

**Step 1: Write failing test**

```python
async def test_memory_context_includes_today_and_yesterday(tmp_path):
    manager = MemoryManager(tmp_path, today_provider=lambda: date(2026, 5, 15))
    manager.daily_store.write(date(2026, 5, 14), "# Yesterday\n- Previous context\n")
    manager.daily_store.write(date(2026, 5, 15), "# Today\n- Current context\n")

    context = await manager.build_context()

    assert "Previous context" in context
    assert "Current context" in context
```

**Step 2: Run the test**

Run: `uv run pytest tests/test_memory.py::test_memory_context_includes_today_and_yesterday -v`

Expected: FAIL because only today's note is loaded.

**Step 3: Implement context loading**

In `MemoryManager.build_context`, read:

- `self.today()`
- `self.today() - timedelta(days=1)`

Render them as separate sections:

- `[Yesterday's Daily Memory]`
- `[Today's Daily Memory]`

Keep the existing priority warning that current conversation wins over memory.

**Step 4: Run tests**

Run: `uv run pytest tests/test_memory.py -v`

Expected: PASS.

## Task 3: Add `memory_get` Tool

**Files:**
- Create: `claw/builtin_tools/memory_tools.py`
- Modify: `claw/builtin_tools/__init__.py`
- Test: `tests/test_memory_tools.py`

**Step 1: Write failing tests**

Test that `memory_get` can read:

- `long`
- `today`
- `yesterday`
- a specific date like `2026-05-15`

Use a temporary `MemoryManager` and a `ToolsRegistry`.

**Step 2: Run the tests**

Run: `uv run pytest tests/test_memory_tools.py -v`

Expected: FAIL because the module does not exist.

**Step 3: Implement tool registration**

Create `register_memory_tools(registry, manager)` that registers:

```python
async def memory_get(kind: str = "long", date: str | None = None) -> str:
    ...
```

Use strict date parsing with `datetime.date.fromisoformat`.

Return clear empty-state strings instead of raising for missing files.

**Step 4: Wire into app creation**

In `chat/app.py`, after `manager = MemoryManager()`, call `register_memory_tools(registry, manager)`.

**Step 5: Run tests**

Run: `uv run pytest tests/test_memory_tools.py tests/test_tools.py -v`

Expected: PASS.

## Task 4: Add Simple `memory_search` Tool

**Files:**
- Modify: `claw/builtin_tools/memory_tools.py`
- Test: `tests/test_memory_tools.py`

**Step 1: Write failing tests**

Create several memory files and assert search returns matching file path snippets:

```python
async def test_memory_search_finds_long_and_daily_notes(tmp_path):
    ...
    result = await registry.execute("memory_search", {"query": "routing"})
    assert "MEMORY.md" in result
    assert "2026-05-15.md" in result
```

**Step 2: Run the test**

Run: `uv run pytest tests/test_memory_tools.py::test_memory_search_finds_long_and_daily_notes -v`

Expected: FAIL because `memory_search` is not registered.

**Step 3: Implement lexical search**

Search only under the memory root. Include:

- `MEMORY.md`
- all `*.md` files under the daily-note location

Return top matches by simple occurrence count. Include file name and a clipped line snippet.

**Step 4: Run tests**

Run: `uv run pytest tests/test_memory_tools.py -v`

Expected: PASS.

## Task 5: Add Dreaming Review Log

**Files:**
- Modify: `claw/memory/store.py`
- Modify: `claw/memory/manager.py`
- Test: `tests/test_memory.py`

**Step 1: Write failing test**

Assert distillation appends a review entry to `DREAMS.md`:

```python
async def test_distill_writes_dreams_review_log(tmp_path):
    manager = MemoryManager(tmp_path, today_provider=lambda: date(2026, 5, 15))
    manager.daily_store.write(date(2026, 5, 15), "## Long-Term Candidates\n- User prefers small plans.\n")

    await manager.distill_daily_to_long_term()

    dreams = (tmp_path / "DREAMS.md").read_text(encoding="utf-8")
    assert "User prefers small plans." in dreams
    assert "accepted" in dreams
```

**Step 2: Run the test**

Run: `uv run pytest tests/test_memory.py::test_distill_writes_dreams_review_log -v`

Expected: FAIL because no `DREAMS.md` exists.

**Step 3: Implement `DreamsStore`**

Add a Markdown append-only store at `root / "DREAMS.md"`.

Each entry should include:

- timestamp
- source date
- candidate text
- score
- status: `accepted`, `duplicate`, or `skipped`
- reason

**Step 4: Update distillation**

In `distill_daily_to_long_term`, compute deterministic scores:

- `0.9` for explicit preference/remember/decision language
- `0.7` for task or project facts
- `0.4` for generic temporary context

Accept candidates with score `>= 0.7` if not duplicate.

**Step 5: Run tests**

Run: `uv run pytest tests/test_memory.py -v`

Expected: PASS.

## Task 6: Integrate With Scheduled Tasks

**Files:**
- Modify: `docs/plans/2026-05-15-scheduled-task-system.md` if needed
- Future create: `claw/scheduler/tasks.py`
- Test: `tests/test_scheduler_tasks.py`

**Step 1: Preserve task names**

Keep the planned tasks:

- `daily_memory_distill`
- `periodic_memory_update`
- `idle_auto_compact`

**Step 2: Define task context contract**

Scheduler task context must include:

- `memory_manager`
- `session_store`
- `gateway`
- `routing_message_factory` or explicit `peer_key`

Do not let generic tasks guess which session to operate on.

**Step 3: Add tests around active session behavior**

Test that `periodic_memory_update` returns a skipped result when there is no active session, rather than creating a new session.

Test that `idle_auto_compact` compacts only the peer/session associated with the idle event payload.

**Step 4: Run scheduler tests**

Run: `uv run pytest tests/test_scheduler*.py -v`

Expected: PASS.

## Task 7: Update Documentation

**Files:**
- Modify: `README.md`
- Optional create: `docs/memory.md`

**Step 1: Document memory files**

Describe:

- `MEMORY.md`
- daily notes
- `DREAMS.md`
- `memory_get`
- `memory_search`

**Step 2: Document priority semantics**

State clearly that current user instruction and current session history outrank memory.

**Step 3: Run docs-adjacent tests**

Run: `uv run pytest tests/ -v`

Expected: PASS.

## Final Verification

Run:

```powershell
uv run pytest tests/test_memory.py tests/test_gateway.py tests/test_deepseek.py -v
uv run pytest tests/ -v
```

Manual CLI check:

```text
/memory today
/memory long
/memory distill
```

Tool check through a model-capable run:

```text
Search memory for my routing preference.
```

Expected: the agent can call `memory_search` and cite relevant memory snippets.
