from __future__ import annotations

import asyncio

from agent_runtime import FakeLLMProvider
from agent_runtime.seat_soul_agent import SeatSoulAgent
from stores.trace_store import InMemoryTraceStore
from tests.agent_runtime.test_llm_agent import _werewolf_night_ctx


def test_seat_soul_agent_routes_by_context_agent_id_and_records_trace_soul_id():
    store = InMemoryTraceStore()
    provider = FakeLLMProvider('{"action_type": "night_kill_nominate", "target": "P3"}')
    agent = SeatSoulAgent(
        provider,
        seat_souls={"P1": "cautious", "P2": "aggressive"},
        model_config={"temperature": 0.2},
        trace_store=store,
    )

    ctx1 = _werewolf_night_ctx()
    ctx2 = ctx1.model_copy(update={"agent_id": "P2"})
    asyncio.run(agent.act(ctx1.model_dump(mode="json")))
    asyncio.run(agent.act(ctx2.model_dump(mode="json")))

    traces = store.list_by_game("u")
    assert [trace.agent_id for trace in traces] == ["P1", "P2"]
    assert [trace.decision_quality_flags["soul_id"] for trace in traces] == [
        "cautious",
        "aggressive",
    ]


def test_seat_soul_agent_missing_seat_falls_back_to_default_soul():
    """没给该座位配 soul → 回退到 DEFAULT_SOUL_ID，不再报错。"""
    from agent_runtime.prompt_template_loader import DEFAULT_SOUL_ID

    store = InMemoryTraceStore()
    provider = FakeLLMProvider('{"action_type": "night_kill_nominate", "target": "P3"}')
    agent = SeatSoulAgent(provider, seat_souls={"P2": "aggressive"}, trace_store=store)

    # P1 不在 seat_souls 里 → 用默认人格，不抛异常
    asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    trace = store.list_by_game("u")[0]
    assert trace.agent_id == "P1"
    assert trace.decision_quality_flags["soul_id"] == DEFAULT_SOUL_ID
