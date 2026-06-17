"""测试 claw/agent_runtime 模块（types + store + factory + resolver）。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from claw.agent_runtime.factory import AgentFactory
from claw.agent_runtime.resolver import AgentResolver
from claw.agent_runtime.store import SqliteAgentStore
from claw.agent_runtime.types import AgentConfig, RuntimeProfile
from claw.expert.store import SqliteExpertStore
from claw.expert.types import Expert, ExpertMeta
from claw.storage.sqlite import get_connection, init_db
from claw.tools import Tool, ToolsRegistry


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = get_connection(tmp_path / "test.sqlite")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def expert_store(conn: sqlite3.Connection) -> SqliteExpertStore:
    return SqliteExpertStore(conn)


@pytest.fixture
def agent_store(conn: sqlite3.Connection) -> SqliteAgentStore:
    return SqliteAgentStore(conn)


def _make_agent(agent_id: str = "ag_test", **overrides) -> AgentConfig:
    defaults = dict(
        id=agent_id,
        name="Test Agent",
        source_expert="general-assistant",
        system_prompt="You are a test agent.",
        enabled_tools=["calculator", "file_search"],
        model_config={"provider": "deepseek", "name": "deepseek-chat"},
        memory_config={"enabled": True},
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _seed_expert(expert_store: SqliteExpertStore) -> None:
    expert_store.save(Expert(
        name="code-helper",
        display_name="Code Helper",
        description="Code expert",
        system_prompt="You help with code.",
        default_tools=["calculator", "file_search"],
        default_skills=["code-review"],
        meta=ExpertMeta(tags=["code"]),
    ))


# ---- Types ----

class TestAgentConfig:
    def test_defaults(self) -> None:
        agent = AgentConfig(
            id="ag_test",
            name="Test",
            source_expert="general-assistant",
            system_prompt="Test",
        )
        assert agent.enabled_skills == []
        assert agent.enabled_tools == []
        assert agent.model_config == {}
        assert agent.created_at == ""

    def test_all_fields(self) -> None:
        agent = _make_agent()
        assert agent.id == "ag_test"
        assert agent.source_expert == "general-assistant"
        assert "calculator" in agent.enabled_tools


class TestRuntimeProfile:
    def test_defaults(self) -> None:
        profile = RuntimeProfile(agent_id="test", system_prompt="prompt")
        assert profile.model_config == {}
        assert profile.enabled_skills == []

    def test_from_agent(self, agent_store: SqliteAgentStore) -> None:
        agent = _make_agent()
        resolver = AgentResolver(agent_store)
        profile = resolver._to_profile(agent)
        assert profile.agent_id == agent.id
        assert profile.system_prompt == agent.system_prompt
        assert profile.enabled_tools == agent.enabled_tools
        assert profile.model_config == agent.model_config


# ---- Store ----

class TestSqliteAgentStore:
    def test_save_and_get(self, agent_store: SqliteAgentStore) -> None:
        agent = _make_agent()
        agent_store.save(agent)
        loaded = agent_store.get("ag_test")
        assert loaded is not None
        assert loaded.name == "Test Agent"
        assert loaded.system_prompt == "You are a test agent."
        assert loaded.enabled_tools == ["calculator", "file_search"]

    def test_get_nonexistent(self, agent_store: SqliteAgentStore) -> None:
        assert agent_store.get("nonexistent") is None

    def test_list_all(self, agent_store: SqliteAgentStore) -> None:
        agent_store.save(_make_agent("ag_a"))
        agent_store.save(_make_agent("ag_b"))
        agents = agent_store.list_all()
        assert len(agents) == 2

    def test_delete(self, agent_store: SqliteAgentStore) -> None:
        agent_store.save(_make_agent())
        assert agent_store.delete("ag_test") is True
        assert agent_store.get("ag_test") is None

    def test_delete_nonexistent(self, agent_store: SqliteAgentStore) -> None:
        assert agent_store.delete("nonexistent") is False

    def test_exists(self, agent_store: SqliteAgentStore) -> None:
        assert agent_store.exists("ag_test") is False
        agent_store.save(_make_agent())
        assert agent_store.exists("ag_test") is True

    def test_save_upsert(self, agent_store: SqliteAgentStore) -> None:
        agent_store.save(_make_agent(system_prompt="v1"))
        agent_store.save(_make_agent(system_prompt="v2"))
        loaded = agent_store.get("ag_test")
        assert loaded is not None
        assert loaded.system_prompt == "v2"

    def test_ensure_default_creates(self, agent_store: SqliteAgentStore) -> None:
        agent = agent_store.ensure_default()
        assert agent.id == "default-agent"
        assert agent.name == "Default Agent"
        assert "calculator" in agent.enabled_tools

    def test_ensure_default_idempotent(self, agent_store: SqliteAgentStore) -> None:
        a1 = agent_store.ensure_default()
        a2 = agent_store.ensure_default()
        assert a1.id == a2.id
        # 不应创建重复记录
        agents = agent_store.list_all()
        default_count = sum(1 for a in agents if a.id == "default-agent")
        assert default_count == 1

    def test_json_fields_preserved(self, agent_store: SqliteAgentStore) -> None:
        agent = _make_agent(
            model_config={"provider": "deepseek", "temperature": 0.5},
            memory_config={"enabled": True, "max_entries": 100},
        )
        agent_store.save(agent)
        loaded = agent_store.get("ag_test")
        assert loaded is not None
        assert loaded.model_config["temperature"] == 0.5
        assert loaded.memory_config["max_entries"] == 100

    def test_created_at_updated_at_set(self, agent_store: SqliteAgentStore) -> None:
        agent = _make_agent()
        agent_store.save(agent)
        loaded = agent_store.get("ag_test")
        assert loaded is not None
        assert loaded.created_at != ""
        assert loaded.updated_at != ""


# ---- Factory ----

class TestAgentFactory:
    def test_create_from_expert(
        self, expert_store: SqliteExpertStore, agent_store: SqliteAgentStore
    ) -> None:
        _seed_expert(expert_store)
        factory = AgentFactory(expert_store, agent_store)
        agent = factory.create_from_expert("code-helper")
        assert agent.id.startswith("ag_")
        assert agent.name == "Code Helper"
        assert agent.source_expert == "code-helper"
        assert agent.system_prompt == "You help with code."
        assert agent.enabled_tools == ["calculator", "file_search"]
        assert agent.enabled_skills == ["code-review"]

    def test_create_with_custom_name(
        self, expert_store: SqliteExpertStore, agent_store: SqliteAgentStore
    ) -> None:
        _seed_expert(expert_store)
        factory = AgentFactory(expert_store, agent_store)
        agent = factory.create_from_expert("code-helper", agent_name="My Code Helper")
        assert agent.name == "My Code Helper"

    def test_create_nonexistent_expert(
        self, expert_store: SqliteExpertStore, agent_store: SqliteAgentStore
    ) -> None:
        factory = AgentFactory(expert_store, agent_store)
        with pytest.raises(ValueError, match="Expert 不存在"):
            factory.create_from_expert("nonexistent")

    def test_create_duplicate_returns_existing(
        self, expert_store: SqliteExpertStore, agent_store: SqliteAgentStore
    ) -> None:
        """同一 Expert 重复调用返回已有 Agent（幂等）。"""
        _seed_expert(expert_store)
        factory = AgentFactory(expert_store, agent_store)
        a1 = factory.create_from_expert("code-helper", agent_name="Agent 1")
        a2 = factory.create_from_expert("code-helper", agent_name="Agent 2")
        assert a1.id == a2.id
        assert a2.name == "Agent 1"  # 保留首次创建的名称
        agents = agent_store.list_all()
        assert len(agents) == 1

    def test_agent_independent_of_expert(
        self, expert_store: SqliteExpertStore, agent_store: SqliteAgentStore
    ) -> None:
        """创建后的 Agent 与 Expert 解耦：修改 Expert 不影响已创建的 Agent。"""
        _seed_expert(expert_store)
        factory = AgentFactory(expert_store, agent_store)
        agent = factory.create_from_expert("code-helper")
        original_prompt = agent.system_prompt

        # 修改 Expert
        expert = expert_store.get("code-helper")
        expert.system_prompt = "Modified expert prompt"
        expert_store.save(expert)

        # Agent 不受影响
        loaded = agent_store.get(agent.id)
        assert loaded is not None
        assert loaded.system_prompt == original_prompt


# ---- Resolver ----

class TestAgentResolver:
    def test_resolve_existing_agent(self, agent_store: SqliteAgentStore) -> None:
        agent_store.save(_make_agent("ag_test", system_prompt="Custom prompt"))
        resolver = AgentResolver(agent_store)
        profile = resolver.resolve("ag_test")
        assert profile.agent_id == "ag_test"
        assert profile.system_prompt == "Custom prompt"

    def test_resolve_none_falls_back_to_default(self, agent_store: SqliteAgentStore) -> None:
        agent_store.ensure_default()
        resolver = AgentResolver(agent_store)
        profile = resolver.resolve(None)
        assert profile.agent_id == "default-agent"

    def test_resolve_nonexistent_falls_back_to_default(self, agent_store: SqliteAgentStore) -> None:
        agent_store.ensure_default()
        resolver = AgentResolver(agent_store)
        profile = resolver.resolve("nonexistent")
        assert profile.agent_id == "default-agent"

    def test_resolve_creates_default_if_missing(self, agent_store: SqliteAgentStore) -> None:
        resolver = AgentResolver(agent_store)
        profile = resolver.resolve(None)
        assert profile.agent_id == "default-agent"
        # 验证已持久化
        assert agent_store.get("default-agent") is not None

    def test_profile_is_deep_copy(self, agent_store: SqliteAgentStore) -> None:
        agent_store.save(_make_agent("ag_test", enabled_tools=["a", "b"]))
        resolver = AgentResolver(agent_store)
        profile = resolver.resolve("ag_test")
        # 修改 profile 不影响原 agent
        profile.enabled_tools.append("c")
        agent = agent_store.get("ag_test")
        assert len(agent.enabled_tools) == 2

    def test_default_agent_modifiable(self, agent_store: SqliteAgentStore) -> None:
        agent_store.ensure_default()
        # 修改 default-agent
        agent = agent_store.get("default-agent")
        agent.system_prompt = "Modified default"
        agent_store.save(agent)

        resolver = AgentResolver(agent_store)
        profile = resolver.resolve(None)
        assert profile.system_prompt == "Modified default"


# ---- Sandbox ----

class TestSandboxRoot:
    """测试 agent sandbox 的解析和隔离。"""

    def test_default_workspace_path(self, agent_store: SqliteAgentStore, tmp_path: Path) -> None:
        """未配置 sandbox_root 时自动分配 data/sandboxes/{agent_id}。"""
        agent = _make_agent(sandbox_config={})
        resolver = AgentResolver(agent_store)
        profile = resolver._to_profile(agent)
        assert profile.sandbox_root.endswith(f"data{os.sep}sandboxes{os.sep}{agent.id}")
        assert Path(profile.sandbox_root).is_dir()

    def test_configured_sandbox_root(self, agent_store: SqliteAgentStore, tmp_path: Path) -> None:
        """sandbox_config.sandbox_root 优先于默认路径。"""
        custom = str(tmp_path / "my-workspace")
        agent = _make_agent(sandbox_config={"sandbox_root": custom})
        resolver = AgentResolver(agent_store)
        profile = resolver._to_profile(agent)
        assert profile.sandbox_root == str(Path(custom).resolve())
        assert Path(profile.sandbox_root).is_dir()

    def test_workspace_dir_auto_created(self, agent_store: SqliteAgentStore, tmp_path: Path) -> None:
        """sandbox 目录不存在时自动创建。"""
        ws = tmp_path / "auto-created-ws"
        assert not ws.exists()
        agent = _make_agent(sandbox_config={"sandbox_root": str(ws)})
        resolver = AgentResolver(agent_store)
        profile = resolver._to_profile(agent)
        assert Path(profile.sandbox_root).is_dir()

    def test_resolve_populates_sandbox_root(self, agent_store: SqliteAgentStore) -> None:
        """resolve() 返回的 RuntimeProfile 包含 sandbox_root。"""
        agent_store.save(_make_agent(sandbox_config={}))
        resolver = AgentResolver(agent_store)
        profile = resolver.resolve("ag_test")
        assert profile.sandbox_root != ""
        assert Path(profile.sandbox_root).is_dir()

    def test_default_agent_has_workspace(self, agent_store: SqliteAgentStore) -> None:
        """default-agent 自动获得 sandbox。"""
        resolver = AgentResolver(agent_store)
        profile = resolver.resolve(None)
        assert profile.sandbox_root != ""
        assert Path(profile.sandbox_root).is_dir()

    def test_factory_creates_workspace(
        self, expert_store: SqliteExpertStore, agent_store: SqliteAgentStore,
    ) -> None:
        """AgentFactory 从 Expert 创建 Agent 时自动分配 sandbox。"""
        _seed_expert(expert_store)
        factory = AgentFactory(expert_store, agent_store)
        agent = factory.create_from_expert("code-helper")
        assert "sandbox_root" in agent.sandbox_config
        assert agent.sandbox_config["sandbox_root"] != ""
        ws_path = Path(agent.sandbox_config["sandbox_root"]).resolve()
        assert ws_path.is_dir()


class TestToolsSandboxIsolation:
    """测试工具在动态 sandbox 下的隔离行为。"""

    @pytest.mark.asyncio
    async def test_file_ops_use_dynamic_workspace(self, tmp_path: Path) -> None:
        """file_ops 工具使用 _sandbox_root 参数，而非注册时的默认值。"""
        from claw.builtin_tools.file_ops import register as register_file_ops

        # 注册时使用默认 sandbox（当前目录）
        registry = ToolsRegistry()
        register_file_ops(registry)

        # 动态 sandbox 下写入文件
        ws = tmp_path / "agent-ws"
        ws.mkdir()
        result = await registry.execute(
            "file_write",
            {"path": "test.txt", "content": "hello"},
            _sandbox_root=str(ws),
        )
        assert "OK" in result
        assert (ws / "test.txt").read_text() == "hello"

        # 读取文件也使用动态 sandbox
        content = await registry.execute(
            "file_read",
            {"path": "test.txt"},
            _sandbox_root=str(ws),
        )
        assert "hello" in content

    @pytest.mark.asyncio
    async def test_file_ops_fallback_without_dynamic_workspace(self, tmp_path: Path) -> None:
        """未注入 _sandbox_root 时回退到注册时的 sandbox_root。"""
        from claw.builtin_tools.file_ops import register as register_file_ops

        ws = tmp_path / "static-ws"
        ws.mkdir()
        registry = ToolsRegistry()
        register_file_ops(registry, sandbox_root=str(ws))

        result = await registry.execute(
            "file_write",
            {"path": "test.txt", "content": "static"},
        )
        assert "OK" in result
        assert (ws / "test.txt").read_text() == "static"

    @pytest.mark.asyncio
    async def test_file_search_uses_dynamic_workspace(self, tmp_path: Path) -> None:
        """file_search 工具使用动态 sandbox。"""
        from claw.builtin_tools.file_search import register as register_file_search

        ws = tmp_path / "search-ws"
        ws.mkdir()
        (ws / "findme.txt").write_text("hello world")

        registry = ToolsRegistry()
        register_file_search(registry)

        result = await registry.execute(
            "file_search",
            {"glob": "*.txt"},
            _sandbox_root=str(ws),
        )
        assert "findme.txt" in result

    @pytest.mark.asyncio
    async def test_file_patch_uses_dynamic_workspace(self, tmp_path: Path) -> None:
        """file_patch 工具使用动态 sandbox。"""
        from claw.builtin_tools.file_patch import register as register_file_patch

        ws = tmp_path / "patch-ws"
        ws.mkdir()
        (ws / "code.py").write_text("x = 1\ny = 2\n")

        registry = ToolsRegistry()
        register_file_patch(registry)

        result = await registry.execute(
            "file_patch",
            {"path": "code.py", "old_text": "x = 1", "new_text": "x = 42"},
            _sandbox_root=str(ws),
        )
        assert "OK" in result
        assert (ws / "code.py").read_text() == "x = 42\ny = 2\n"

    @pytest.mark.asyncio
    async def test_file_ops_path_escape_blocked(self, tmp_path: Path) -> None:
        """动态 sandbox 下路径逃逸仍被阻止。"""
        from claw.builtin_tools.file_ops import register as register_file_ops

        ws = tmp_path / "safe-ws"
        ws.mkdir()

        registry = ToolsRegistry()
        register_file_ops(registry)

        result = await registry.execute(
            "file_read",
            {"path": "../../etc/passwd"},
            _sandbox_root=str(ws),
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_tools_registry_execute_merges_kwargs(self) -> None:
        """ToolsRegistry.execute(**extra_kwargs) 合并到 tool args 中。"""
        registry = ToolsRegistry()

        async def echo_handler(args: dict) -> dict:
            return args

        registry.register(Tool(
            name="echo",
            description="echo args",
            handler=echo_handler,
        ))

        result = await registry.execute(
            "echo",
            {"a": 1},
            _sandbox_root="/tmp/ws",
        )
        assert result["a"] == 1
        assert result["_sandbox_root"] == "/tmp/ws"
