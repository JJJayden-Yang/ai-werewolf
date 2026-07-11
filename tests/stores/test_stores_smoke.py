"""stores 骨架冒烟测试。

第一阶段只验证模块结构能被导入。
EventStore 的 append/list、BeliefStateStore 的 get/save/history 实质测试
在 Task C5-C6 实现 PR 中加。
"""

from stores import (
    AgentTuningTraceStore,
    BeliefStateStore,
    ContextSnapshotStore,
    EventStore,
    PromptVersionRegistry,
    StrategyMemoryStore,
    TraceStore,
)


def test_stores_public_api_present():
    assert EventStore is not None
    assert BeliefStateStore is not None
    assert PromptVersionRegistry is not None
    assert TraceStore is not None
    assert AgentTuningTraceStore is not None
    assert StrategyMemoryStore is not None
    assert ContextSnapshotStore is not None
