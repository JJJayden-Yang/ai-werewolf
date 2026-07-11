"""agent_runtime 骨架冒烟测试。

第一阶段只验证模块结构能被导入、对外接口存在。
具体逻辑测试在各 Task 实现 PR 中加（test_action_canonicalizer_wraps_quarrel_as_speak /
test_action_guard_blocks_meta_ai / 等）。
"""

from agent_runtime import (
    ActionCanonicalizer,
    ActionParser,
    FakeLLMProvider,
    FallbackPolicy,
    LLMAdapter,
    LLMProvider,
    LLMProviderRegistry,
    LLMResponse,
    PromptTemplate,
    PromptTemplateLoader,
    RetryPolicy,
)


def test_agent_runtime_public_api_present():
    """对外接口骨架齐全：实例化不报错（方法体可为 NotImplementedError）。"""
    assert LLMProvider is not None
    assert FakeLLMProvider is not None
    assert LLMProviderRegistry is not None
    assert LLMAdapter is not None
    assert PromptTemplateLoader is not None
    assert ActionParser is not None
    assert ActionCanonicalizer is not None
    assert RetryPolicy is not None
    assert FallbackPolicy is not None
    assert LLMResponse is not None
    assert PromptTemplate is not None
