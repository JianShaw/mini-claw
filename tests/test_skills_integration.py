"""端到端集成测试：验证两层技能加载机制。

Layer 1（系统提示）：轻量级技能索引（name + description）
Layer 2（tool_result）：LLM 通过 load_skill 工具按需加载完整指令
"""

from __future__ import annotations

import pytest

from claw.builtin_tools.skill_loader import _load_skill
from claw.deepseek import DeepSeekAgentRunner
from claw.gateway import RuntimeGateway
from claw.session import InMemorySessionStore
from claw.skills.registry import SkillsRegistry
from claw.skills.types import Skill, SkillMeta
from claw.types import InboundMessage, Session, StreamChunk


def _make_session() -> Session:
    return Session(
        session_id="test-sess",
        session_key="cli:local:user",
        channel="cli",
        account_id="local",
        peer_id="user",
        sender_id="user",
        agent_id="default-agent",
    )


def _make_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        channel="cli",
        account_id="local",
        peer_id="user",
        sender_id="user",
        message_id="msg-1",
        text=text,
        timestamp=0,
        message_type="text",
        raw=None,
    )


def _make_skill(name: str = "test-skill", **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.get("description", f"{name} description"),
        instructions=kw.get("instructions", f"You are now using the {name} skill."),
        tools=kw.get("tools", []),
        meta=kw.get("meta", SkillMeta()),
    )


class TestSkillContextInjection:
    """验证 Layer 1（轻量级索引）注入到 LLM messages。"""

    def test_skills_listing_appears_in_build_messages(self):
        """技能索引应注入到 _build_messages 的输出中。"""
        runner = DeepSeekAgentRunner(api_key="fake")
        session = _make_session()
        session.metadata["skills_listing"] = "## Available Skills\n\n- **translate**: Translate text"

        messages = runner._build_messages(session)
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert any("Available Skills" in m["content"] for m in system_msgs)

    def test_no_skill_instructions_in_system_prompt(self):
        """完整技能指令不应出现在系统提示词中。"""
        runner = DeepSeekAgentRunner(api_key="fake")
        session = _make_session()
        session.metadata["skills_listing"] = "## Available Skills\n\n- **pdf**: Process PDF"

        messages = runner._build_messages(session)
        system_msgs = [m for m in messages if m["role"] == "system"]
        # 只有 skills_listing，不应有 skill_instructions
        assert len(system_msgs) == 1
        assert "Available Skills" in system_msgs[0]["content"]

    def test_injection_order_summary_memory_listing(self):
        """系统提示词注入顺序：summary → memory_context → skills_listing。"""
        runner = DeepSeekAgentRunner(api_key="fake")
        session = _make_session()
        session.summary = "Summary text"
        session.metadata["memory_context"] = "Memory context text"
        session.metadata["skills_listing"] = "Skills listing text"

        messages = runner._build_messages(session)
        system_msgs = [m for m in messages if m["role"] == "system"]

        assert len(system_msgs) >= 3
        contents = [m["content"] for m in system_msgs]
        summary_idx = next(i for i, c in enumerate(contents) if "Summary text" in c)
        memory_idx = next(i for i, c in enumerate(contents) if "Memory context text" in c)
        listing_idx = next(i for i, c in enumerate(contents) if "Skills listing text" in c)
        assert summary_idx < memory_idx < listing_idx

    def test_no_skill_info_when_no_skills(self):
        """无技能时，_build_messages 不包含技能相关 system message。"""
        runner = DeepSeekAgentRunner(api_key="fake")
        session = _make_session()

        messages = runner._build_messages(session)
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) == 0


class TestGatewaySkillInjection:
    """验证 Gateway 只注入轻量级索引。"""

    @pytest.mark.asyncio
    async def test_listing_always_injected(self):
        """有技能注册时，Gateway 注入 skills_listing。"""
        registry = SkillsRegistry()
        registry.register(_make_skill("translate"))
        registry.register(_make_skill("code-review"))

        session = _make_session()

        gateway = RuntimeGateway(
            session_store=InMemorySessionStore(),
            agent_runner=_FakeRunner(),
            delivery=_FakeDelivery(),
            skills_registry=registry,
        )

        msg = _make_message("hello")
        await gateway._inject_skill_context(session, msg)

        assert "skills_listing" in session.metadata
        assert "translate" in session.metadata["skills_listing"]
        assert "code-review" in session.metadata["skills_listing"]

    @pytest.mark.asyncio
    async def test_no_registry_no_injection(self):
        """无注册表时，清理旧的技能信息。"""
        session = _make_session()
        session.metadata["skills_listing"] = "old listing"

        gateway = RuntimeGateway(
            session_store=InMemorySessionStore(),
            agent_runner=_FakeRunner(),
            delivery=_FakeDelivery(),
        )

        msg = _make_message()
        await gateway._inject_skill_context(session, msg)

        assert "skills_listing" not in session.metadata


class TestLoadSkillTool:
    """验证 load_skill 工具的 Layer 2 加载。"""

    @pytest.mark.asyncio
    async def test_load_skill_returns_instructions(self):
        """load_skill 返回格式化的完整指令。"""
        registry = SkillsRegistry()
        registry.register(Skill(
            name="pdf",
            description="Process PDF files",
            instructions="Step 1: Read PDF\nStep 2: Extract text",
            tools=["file_read"],
        ))

        # 模拟 skill_loader 注册后的调用
        from claw.builtin_tools import skill_loader
        skill_loader._skills_registry = registry

        result = await _load_skill({"name": "pdf"})
        assert '<skill name="pdf">' in result
        assert "Step 1: Read PDF" in result
        assert "file_read" in result
        assert "</skill>" in result

    @pytest.mark.asyncio
    async def test_load_skill_nonexistent(self):
        """加载不存在的技能返回错误信息。"""
        registry = SkillsRegistry()
        registry.register(_make_skill("translate"))

        from claw.builtin_tools import skill_loader
        skill_loader._skills_registry = registry

        result = await _load_skill({"name": "nonexistent"})
        assert "Error" in result
        assert "not found" in result
        assert "translate" in result  # 列出可用技能

    @pytest.mark.asyncio
    async def test_load_skill_no_name(self):
        """不传 name 参数返回错误。"""
        registry = SkillsRegistry()
        from claw.builtin_tools import skill_loader
        skill_loader._skills_registry = registry

        result = await _load_skill({})
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_load_skill_instructions_not_in_system_prompt(self):
        """技能指令通过 tool_result 返回，不出现在系统提示词中。"""
        registry = SkillsRegistry()
        registry.register(Skill(
            name="big-skill",
            description="A skill with lots of instructions",
            instructions="Very long instructions " * 100,
        ))

        # 注入 Layer 1
        session = _make_session()
        gateway = RuntimeGateway(
            session_store=InMemorySessionStore(),
            agent_runner=_FakeRunner(),
            delivery=_FakeDelivery(),
            skills_registry=registry,
        )
        msg = _make_message()
        await gateway._inject_skill_context(session, msg)

        # 验证系统提示词只有轻量索引
        runner = DeepSeekAgentRunner(api_key="fake")
        messages = runner._build_messages(session)
        system_msgs = [m for m in messages if m["role"] == "system"]
        for sm in system_msgs:
            # 系统提示词不应包含完整指令
            assert "Very long instructions" not in sm["content"]

        # 完整指令通过 tool_result 获取
        from claw.builtin_tools import skill_loader
        skill_loader._skills_registry = registry
        tool_result = await _load_skill({"name": "big-skill"})
        assert "Very long instructions" in tool_result


class TestSlashCommandActivation:
    """验证斜杠命令激活技能。"""

    def test_get_by_slash_name(self):
        registry = SkillsRegistry()
        skill = _make_skill("code-review")
        registry.register(skill)

        found = registry.get_by_slash_name("/code-review")
        assert found is not None
        assert found.name == "code-review"

    def test_activate_via_slash_name(self):
        registry = SkillsRegistry()
        skill = _make_skill("code-review")
        registry.register(skill)

        found = registry.get_by_slash_name("/code-review")
        assert found is not None
        registry.activate(found.name)
        assert registry.active_skill is skill


class TestMultipleSkills:
    """验证多技能场景。"""

    def test_only_one_active_at_a_time(self):
        registry = SkillsRegistry()
        for name in ["a", "b", "c"]:
            registry.register(_make_skill(name))

        registry.activate("a")
        assert registry.active_skill.name == "a"
        registry.activate("b")
        assert registry.active_skill.name == "b"
        registry.activate("c")
        assert registry.active_skill.name == "c"

    def test_listing_includes_all_skills(self):
        registry = SkillsRegistry()
        registry.register(_make_skill("translate", description="Translate text"))
        registry.register(_make_skill("code-review", description="Review code"))
        registry.register(_make_skill("summarize", description="Summarize text"))

        listing = registry.build_skills_listing()
        assert "translate" in listing
        assert "code-review" in listing
        assert "summarize" in listing


# --- Fake implementations ---

class _FakeRunner:
    async def run(self, session, message):
        from claw.types import AgentReply
        return AgentReply(text="fake reply")

    async def run_stream(self, session, message):
        yield StreamChunk(type="content", text="fake reply")


class _FakeDelivery:
    async def send(self, message, reply):
        pass
