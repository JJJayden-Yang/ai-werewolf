"""LLMProvider / FakeLLMProvider / LLMProviderRegistry 测试（C1）。

覆盖：

- 抽象层  LLMProvider 不可直接实例化（ABC 行为）
- FakeLLMProvider 三种响应规格（str / list[str] / Callable）的行为与边界
- LLMResponse 字段构造（model_name / latency_ms / token_usage / metadata 的来源与默认）
- LLMProviderRegistry 的 register / get / unregister 语义与异常
- generate_sync 便利包装

不用 ``pytest-asyncio`` —— 直接 ``asyncio.run()`` 包一层即可，避免引入 dev 依赖
（``requirements-dev.txt`` 当前只有 ``pytest>=8`` 和 ``pydantic>=2.6``）。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.exceptions import (
    AgentRuntimeError,
    FakeLLMExhaustedError,
    LLMProviderNotFoundError,
)
from agent_runtime.llm_provider import (
    FakeLLMProvider,
    LLMProvider,
    LLMProviderRegistry,
    generate_sync,
)
from agent_runtime.types import LLMResponse


# ---------- 工具 ----------


def _gen(provider: LLMProvider, messages=None, config=None) -> LLMResponse:
    """同步调用 ``provider.generate``，返回 LLMResponse。

    测试便利包装：避免每个测试自己写 ``asyncio.run(provider.generate(...))``。
    """
    return asyncio.run(provider.generate(messages or [], config or {}))


# ---------- ABC 行为 ----------


class TestLLMProviderInterface:
    def test_llm_provider_is_abstract(self):
        """LLMProvider 本身不能直接实例化 —— 强制走子类。"""
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_subclasses_must_implement_generate(self):
        """缺 ``generate`` 实现的子类同样不能实例化。"""

        class IncompleteProvider(LLMProvider):  # type: ignore[misc]
            pass  # 故意不实现 generate

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore[abstract]

    def test_generate_is_coroutine(self):
        """LLMProvider.generate 必须是 async（contract 形态）。"""

        provider = FakeLLMProvider(responses="x")
        coro = provider.generate([], {})
        try:
            assert asyncio.iscoroutine(coro)
        finally:
            coro.close()  # 别让事件循环抱怨"coroutine was never awaited"


# ---------- FakeLLMProvider：响应规格 ----------


class TestFakeLLMProviderStringResponses:
    def test_str_response_returned_unchanged(self):
        """str 模式：每次返回同一段文本。"""
        provider = FakeLLMProvider(responses="hello world")
        resp = _gen(provider)
        assert resp.raw_output == "hello world"

    def test_str_response_returned_unlimited(self):
        """str 模式：调用多少次都返回同一段，不会"耗尽"。"""
        provider = FakeLLMProvider(responses="ok")
        for _ in range(10):
            assert _gen(provider).raw_output == "ok"
        assert provider.call_count == 10


class TestFakeLLMProviderListResponses:
    def test_list_responses_consumed_in_order(self):
        """list 模式：按顺序消费每个元素。"""
        provider = FakeLLMProvider(responses=["a", "b", "c"])
        assert _gen(provider).raw_output == "a"
        assert _gen(provider).raw_output == "b"
        assert _gen(provider).raw_output == "c"

    def test_list_exhausted_raises(self):
        """list 模式：用尽后再调一次抛 FakeLLMExhaustedError，暴露"调用次数超预期"。"""
        provider = FakeLLMProvider(responses=["only one"])
        _gen(provider)
        with pytest.raises(FakeLLMExhaustedError) as exc:
            _gen(provider)
        assert exc.value.available == 1
        assert exc.value.called == 2

    def test_fake_llm_exhausted_error_is_agent_runtime_error(self):
        provider = FakeLLMProvider(responses=[])
        with pytest.raises(AgentRuntimeError):
            _gen(provider)

    def test_empty_list_immediately_exhausts(self):
        """空 list 第一次调用就抛 —— 显式比"静默"好。"""
        provider = FakeLLMProvider(responses=[])
        with pytest.raises(FakeLLMExhaustedError):
            _gen(provider)


class TestFakeLLMProviderCallableResponses:
    def test_callable_response_invoked_per_call(self):
        """Callable 模式：每次 generate 都调一次 callable。"""
        calls: list[tuple[list, dict]] = []

        def responder(messages, config):
            calls.append((messages, config))
            return f"call-{len(calls)}"

        provider = FakeLLMProvider(responses=responder)
        assert _gen(provider, [{"role": "u", "content": "a"}], {"k": 1}).raw_output == "call-1"
        assert _gen(provider, [{"role": "u", "content": "b"}], {"k": 2}).raw_output == "call-2"
        assert len(calls) == 2
        # callable 拿到的就是 generate 入参原样透传
        assert calls[0][0] == [{"role": "u", "content": "a"}]
        assert calls[1][1] == {"k": 2}

    def test_callable_returning_llm_response_passed_through(self):
        """Callable 返回完整 LLMResponse 时，FakeLLMProvider 不二次包装，原样透出。"""
        custom = LLMResponse(
            raw_output="canned",
            model_name="custom-model",
            token_usage={"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
            latency_ms=42.0,
            metadata={"trace_id": "t-1"},
        )
        provider = FakeLLMProvider(responses=lambda *_: custom)
        resp = _gen(provider)
        # 同一对象（不复制），最大化"完全可控"
        assert resp is custom
        assert resp.token_usage["total_tokens"] == 18
        assert resp.metadata == {"trace_id": "t-1"}


# ---------- FakeLLMProvider：LLMResponse 字段构造 ----------


class TestFakeLLMResponseFields:
    def test_default_model_name_is_provider_default(self):
        """未传 model_config['model_name'] 时，落回 provider 的默认值。"""
        provider = FakeLLMProvider(responses="ok", model_name="fake-default")
        resp = _gen(provider, config={})
        assert resp.model_name == "fake-default"

    def test_model_config_overrides_model_name(self):
        """传 model_config['model_name'] 时用其覆盖 —— 便于按模型名分桶 trace。"""
        provider = FakeLLMProvider(responses="ok", model_name="fake-default")
        resp = _gen(provider, config={"model_name": "from-config"})
        assert resp.model_name == "from-config"

    def test_latency_ms_passed_through(self):
        """构造时配的 latency_ms 写到响应里（不真正 sleep）。"""
        provider = FakeLLMProvider(responses="ok", latency_ms=12.5)
        resp = _gen(provider)
        assert resp.latency_ms == 12.5

    def test_default_latency_ms_is_zero(self):
        provider = FakeLLMProvider(responses="ok")
        resp = _gen(provider)
        assert resp.latency_ms == 0.0

    def test_token_usage_default_empty(self):
        """Fake 不算 token，默认空 dict（真实 provider 才填）。"""
        provider = FakeLLMProvider(responses="ok")
        resp = _gen(provider)
        assert resp.token_usage == {}

    def test_metadata_marks_fake_provider_kind(self):
        """metadata 默认带 provider_kind=fake；trace 层据此判断"这是 fake 输出"。"""
        provider = FakeLLMProvider(responses="ok")
        resp = _gen(provider)
        assert resp.metadata == {"provider_kind": "fake"}

    def test_call_count_increments(self):
        provider = FakeLLMProvider(responses="ok")
        assert provider.call_count == 0
        _gen(provider)
        assert provider.call_count == 1
        _gen(provider)
        assert provider.call_count == 2


# ---------- FakeLLMProvider：构造时类型校验 ----------


class TestFakeLLMProviderConstruction:
    @pytest.mark.parametrize("bad", [123, 1.5, {"a": "b"}, ("a", "b"), None])
    def test_rejects_invalid_responses_type(self, bad):
        """构造时拒掉非法 responses 类型 —— fail fast，避免运行到 generate 才崩。"""
        with pytest.raises(TypeError, match="responses must be"):
            FakeLLMProvider(responses=bad)  # type: ignore[arg-type]

    def test_rejects_list_with_non_str(self):
        """list 模式只允许 list[str]。"""
        with pytest.raises(TypeError, match="responses list must contain only str"):
            FakeLLMProvider(responses=["ok", 42])  # type: ignore[list-item]


# ---------- LLMProviderRegistry ----------


class TestLLMProviderRegistry:
    def test_register_then_get_roundtrips(self):
        registry = LLMProviderRegistry()
        provider = FakeLLMProvider(responses="ok")
        registry.register("fake-llm", provider)

        assert registry.get("fake-llm") is provider

    def test_get_unknown_raises_provider_not_found(self):
        registry = LLMProviderRegistry()
        with pytest.raises(LLMProviderNotFoundError) as exc:
            registry.get("never-registered")
        assert exc.value.model_name == "never-registered"

    def test_provider_not_found_is_agent_runtime_error(self):
        """LLMProviderNotFoundError 是 AgentRuntimeError 子类 —— 上游一处 catch。"""
        registry = LLMProviderRegistry()
        with pytest.raises(AgentRuntimeError):
            registry.get("nope")

    def test_register_duplicate_raises_value_error(self):
        """同名重复注册抛 ValueError —— 启动期 typo / 重复 init 是常见 bug，
        让它早炸而不是悄悄覆盖。"""
        registry = LLMProviderRegistry()
        registry.register("m1", FakeLLMProvider(responses="a"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register("m1", FakeLLMProvider(responses="b"))

    def test_unregister_removes(self):
        registry = LLMProviderRegistry()
        registry.register("m1", FakeLLMProvider(responses="a"))
        registry.unregister("m1")

        with pytest.raises(LLMProviderNotFoundError):
            registry.get("m1")

    def test_unregister_unknown_is_noop(self):
        """unregister 是幂等：移除一个本就没注册的 model_name 不报错。"""
        registry = LLMProviderRegistry()
        registry.unregister("never-registered")  # 不抛

    def test_can_re_register_after_unregister(self):
        """unregister + register 的组合可以"替换" provider。"""
        registry = LLMProviderRegistry()
        first = FakeLLMProvider(responses="first")
        second = FakeLLMProvider(responses="second")

        registry.register("m1", first)
        registry.unregister("m1")
        registry.register("m1", second)

        assert registry.get("m1") is second

    def test_contains_and_len(self):
        registry = LLMProviderRegistry()
        assert "m1" not in registry
        assert len(registry) == 0

        registry.register("m1", FakeLLMProvider(responses="a"))
        registry.register("m2", FakeLLMProvider(responses="b"))

        assert "m1" in registry
        assert "m2" in registry
        assert len(registry) == 2

    def test_multiple_providers_routed_independently(self):
        """同 Registry 下多 provider，按 model_name 路由到对应实例。"""
        registry = LLMProviderRegistry()
        registry.register("m1", FakeLLMProvider(responses="from-m1"))
        registry.register("m2", FakeLLMProvider(responses="from-m2"))

        resp1 = _gen(registry.get("m1"))
        resp2 = _gen(registry.get("m2"))

        assert resp1.raw_output == "from-m1"
        assert resp2.raw_output == "from-m2"


# ---------- generate_sync 便利包装 ----------


class TestGenerateSync:
    def test_generate_sync_returns_response(self):
        provider = FakeLLMProvider(responses="hi", model_name="fake-default")
        resp = generate_sync(provider, [{"role": "u", "content": "x"}], {})
        assert isinstance(resp, LLMResponse)
        assert resp.raw_output == "hi"
        assert resp.model_name == "fake-default"

    def test_generate_sync_propagates_provider_errors(self):
        """generate_sync 不吞 provider 抛出的异常。"""
        provider = FakeLLMProvider(responses=[])
        with pytest.raises(FakeLLMExhaustedError):
            generate_sync(provider, [], {})


# ---------- 集成：Fake + Registry 协作（薄切片所需 happy path） ----------


class TestFakeAndRegistryIntegration:
    def test_thin_slice_happy_path(self):
        """模拟"MockAgent 跑 6 人局"薄切片的 LLM 路径：
        Registry 路由 → FakeLLMProvider 返回固定脚本 → 调用方读 LLMResponse。

        这个测试在 C1 这一层兜底"接口形状对" + "数据流通"。
        """
        registry = LLMProviderRegistry()
        registry.register(
            "fake-werewolf",
            FakeLLMProvider(
                responses=[
                    '{"action_type": "speak", "public_message": "I am a villager."}',
                    '{"action_type": "vote", "target": "P3"}',
                ],
                model_name="fake-werewolf",
            ),
        )

        provider = registry.get("fake-werewolf")
        first = _gen(provider, [{"role": "system", "content": "..."}])
        second = _gen(provider, [{"role": "system", "content": "..."}])

        assert "speak" in first.raw_output
        assert "vote" in second.raw_output
        assert first.model_name == "fake-werewolf"
        # call_count 反映"被调过 2 次"，便于上游审计
        assert provider.call_count == 2  # type: ignore[attr-defined]
