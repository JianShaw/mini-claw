"""测试 claw/expert/types 模块。"""

from __future__ import annotations

from claw.expert.types import Expert, ExpertMeta


class TestExpertMeta:
    def test_defaults(self) -> None:
        meta = ExpertMeta()
        assert meta.version == "0.1.0"
        assert meta.author == ""
        assert meta.tags == []
        assert meta.category == ""
        assert meta.avatar == ""
        assert meta.extra == {}


class TestExpert:
    def test_defaults(self) -> None:
        expert = Expert(
            name="test-expert",
            display_name="Test Expert",
            description="A test expert",
            system_prompt="You are a test expert.",
        )
        assert expert.name == "test-expert"
        assert expert.source == "local"
        assert expert.default_skills == []
        assert expert.default_tools == []
        assert expert.default_model == {}
        assert expert.meta.version == "0.1.0"

    def test_is_valid_name(self) -> None:
        # 合法名称
        assert Expert.is_valid_name("abc")
        assert Expert.is_valid_name("my-expert")
        assert Expert.is_valid_name("expert123")
        assert Expert.is_valid_name("a")
        # 64 字符刚好允许
        assert Expert.is_valid_name("a" * 64)

        # 非法名称
        assert not Expert.is_valid_name("")
        assert not Expert.is_valid_name("ABC")
        assert not Expert.is_valid_name("1abc")
        assert not Expert.is_valid_name("my_expert")
        assert not Expert.is_valid_name("my.expert")
        assert not Expert.is_valid_name("a" * 65)
        assert not Expert.is_valid_name("-abc")
