"""context 骨架冒烟测试。

第一阶段只验证模块结构能被导入。
信息隔离 / 预算 / Fact Stream 的实质测试在 Task C7-C9 实现 PR 中加
(test_context_has_no_truth_state / test_v0_context_has_no_belief /
test_historical_speech_raw_zero / 等)。
"""

from context import (
    AgentContextDraft,
    ContextAssembler,
    ContextWindowPolicy,
    SpeechSummarizer,
    VisibilityRuleSpec,
)


def test_context_public_api_present():
    assert VisibilityRuleSpec is not None
    assert ContextAssembler is not None
    assert ContextWindowPolicy is not None
    assert SpeechSummarizer is not None
    assert AgentContextDraft is not None
