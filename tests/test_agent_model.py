"""测试 V1 Phase 3b: 动态 model_config — DeepSeekRunner 每轮按 RuntimeProfile 设置 model/temperature。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claw.agent_runtime.types import RuntimeProfile
from claw.deepseek import DeepSeekAgentRunner
from claw.types import Session


def _session_with_profile(
    agent_id: str = "ag_test",
    system_prompt: str = "test",
    model_config: dict[str, Any] | None = None,
) -> Session:
    """构造带 agent_runtime_profile 的 session。"""
    session = Session(
        session_id="sess_1",
        session_key="test:default:test",
        channel="local",
        account_id="default",
        peer_id="test",
        sender_id="test",
        agent_id=agent_id,
    )
    session.metadata["agent_runtime_profile"] = {
        "agent_id": agent_id,
        "system_prompt": system_prompt,
        "model_config": model_config or {},
        "enabled_skills": [],
        "enabled_tools": [],
        "enabled_mcp_servers": [],
        "memory_config": {},
        "sandbox_config": {},
    }
    return session


class TestBuildKwargsDynamicModel:
    def test_default_model_without_session(self) -> None:
        runner = DeepSeekAgentRunner(api_key="test-key", model="base-model")
        kwargs = runner._build_kwargs([{"role": "user", "content": "hi"}])
        assert kwargs["model"] == "base-model"
        assert "temperature" not in kwargs

    def test_profile_model_overrides_default(self) -> None:
        runner = DeepSeekAgentRunner(api_key="test-key", model="base-model")
        session = _session_with_profile(
            model_config={"name": "custom-model", "temperature": 0.5}
        )
        kwargs = runner._build_kwargs([{"role": "user", "content": "hi"}], session)
        assert kwargs["model"] == "custom-model"
        assert kwargs["temperature"] == 0.5

    def test_profile_model_only_name(self) -> None:
        runner = DeepSeekAgentRunner(api_key="test-key", model="base-model")
        session = _session_with_profile(
            model_config={"name": "other-model"}
        )
        kwargs = runner._build_kwargs([{"role": "user", "content": "hi"}], session)
        assert kwargs["model"] == "other-model"
        assert "temperature" not in kwargs

    def test_profile_model_only_temperature(self) -> None:
        runner = DeepSeekAgentRunner(api_key="test-key", model="base-model")
        session = _session_with_profile(
            model_config={"temperature": 0.3}
        )
        kwargs = runner._build_kwargs([{"role": "user", "content": "hi"}], session)
        assert kwargs["model"] == "base-model"
        assert kwargs["temperature"] == 0.3

    def test_empty_model_config_uses_default(self) -> None:
        runner = DeepSeekAgentRunner(api_key="test-key", model="base-model")
        session = _session_with_profile(model_config={})
        kwargs = runner._build_kwargs([{"role": "user", "content": "hi"}], session)
        assert kwargs["model"] == "base-model"
        assert "temperature" not in kwargs

    def test_no_profile_uses_default(self) -> None:
        runner = DeepSeekAgentRunner(api_key="test-key", model="base-model")
        session = Session(
            session_id="s1", session_key="k", channel="local",
            account_id="a", peer_id="p", sender_id="s", agent_id="ag",
        )
        # session 没有 agent_runtime_profile
        kwargs = runner._build_kwargs([{"role": "user", "content": "hi"}], session)
        assert kwargs["model"] == "base-model"
