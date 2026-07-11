"""LLMAgent —— v0 "最后一公里"接线验证（不联网、不花 API）。

用 FakeLLMProvider 替换真实 ArkLLMProvider，证明：
- LLMAgent 把 loader→render→provider→parse→canonicalize 串成 Supervisor 可注入的 act()；
- 注入 Supervisor 后能跑完整 9 人 v0 局（脏输出由 Supervisor 安全兜底，不崩局）；
- LLM 失败 / 解析失败都被吞成空 dict 走兜底，并在 self.stats 里计数（可观测）。
"""

import asyncio
import json
import random
from pathlib import Path

from contracts import (
    ActionType,
    AgentContext,
    GameConfig,
    Phase,
    PlayerStatus,
    Role,
    VisiblePlayer,
)
from context.context_assembler import ContextAssembler
from agent_runtime import FakeLLMProvider, LLMAgent
from agent_runtime.exceptions import AgentRuntimeError
from agent_runtime.types import LLMResponse
from game_core import GameEngine, GameSessionManager
from stores.event_store import InMemoryEventStore
from supervisor import Supervisor

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


def test_llm_agent_runs_full_9p_v0_game_with_fake_provider():
    """注入 LLMAgent(FakeLLM) 的 Supervisor 能跑完整 9 人局到 GAME_OVER。

    FakeLLM 恒返回 skip：合法处用 skip，不合法处（夜刀/查验等）由 Supervisor 兜底成安全
    动作。验证的是接线 + 兜底安全网，而非模型棋力。
    """
    data = json.loads((FIXTURES / "game_config_9p_mvp.json").read_text(encoding="utf-8"))
    data["game_id"] = "llm_agent_full"
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(0))
    engine.sessions.create_game(GameConfig.model_validate(data))
    store = InMemoryEventStore()
    agent = LLMAgent(FakeLLMProvider('{"action_type": "skip", "reason_summary": "t"}'))
    sup = Supervisor(
        engine,
        ContextAssembler(session_provider=engine, event_store=store),
        agent,
        store,
        deliver_witch_kill_info=True,
    )
    asyncio.run(sup.run_game("llm_agent_full"))

    assert engine.get_session("llm_agent_full").current_phase == Phase.GAME_OVER
    # 每次调用都成功 parse 成 skip（identity 由 parser 从 ctx 补齐），无 parse/llm 错误
    assert agent.stats["ok"] > 0
    assert agent.stats["parse_error"] == 0
    assert agent.stats["llm_error"] == 0


def _werewolf_night_ctx() -> AgentContext:
    players = [f"P{i}" for i in range(1, 10)]
    return AgentContext(
        game_id="u",
        agent_id="P1",
        role=Role.WEREWOLF,
        round=1,
        phase=Phase.NIGHT_WEREWOLF,
        visible_players=[
            VisiblePlayer(player_id=p, status=PlayerStatus.ALIVE) for p in players
        ],
        allowed_actions=[ActionType.NIGHT_KILL_NOMINATE],
    )


def test_llm_agent_parses_action_and_fills_identity_from_context():
    """合法 JSON → act() 返回标准 AgentAction dict，identity 字段由 ctx 补齐。"""
    provider = FakeLLMProvider('{"action_type": "night_kill_nominate", "target": "P3"}')
    agent = LLMAgent(provider)
    out = asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    assert out["action_type"] == ActionType.NIGHT_KILL_NOMINATE.value
    assert out["target"] == "P3"
    assert out["agent_id"] == "P1"
    assert out["role"] == Role.WEREWOLF.value
    assert out["game_id"] == "u"
    assert agent.stats == {
        "ok": 1, "parse_error": 0, "llm_error": 0, "retry": 0,
        "canonicalize_meta_ai": 0, "canonicalize_cot_leak": 0, "canonicalize_role_leak": 0,
    }


def test_llm_agent_parse_error_returns_empty_dict_for_fallback():
    """模型输出非 JSON → 返回 {} 让 Supervisor 兜底，parse_error 计数 +1。"""
    agent = LLMAgent(FakeLLMProvider("这不是 JSON，模型胡言乱语"))
    out = asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    assert out == {}
    assert agent.stats["parse_error"] == 1
    assert agent.stats["ok"] == 0


class _BoomProvider:
    """generate 永远抛 AgentRuntimeError，模拟真实 LLM 调用失败（HTTP/auth/timeout）。"""

    async def generate(self, messages: list[dict], model_config: dict) -> LLMResponse:
        raise AgentRuntimeError("simulated provider failure")


class _FlakyProvider:
    """前 fail_times 次抛 AgentRuntimeError，之后返回合法 JSON —— 验证重试能挽回瞬时超时。"""

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self._calls = 0

    async def generate(self, messages: list[dict], model_config: dict) -> LLMResponse:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise AgentRuntimeError("transient ReadTimeout")
        return LLMResponse(
            raw_output='{"action_type": "night_kill_nominate", "target": "P3"}',
            model_name="x",
            token_usage={},
            latency_ms=0.0,
            metadata={},
        )


def test_llm_agent_llm_error_returns_empty_dict_after_retries_exhausted():
    """LLM 持续失败 → 重试耗尽 → 返回 {} 让 Supervisor 兜底，llm_error+1、retry 计数，不崩。"""
    agent = LLMAgent(_BoomProvider(), retry_backoff_seconds=0.0)  # max_retries 默认 2
    out = asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    assert out == {}
    assert agent.stats["llm_error"] == 1
    assert agent.stats["retry"] == 2  # 2 次重试后才放弃
    assert agent.stats["ok"] == 0


def test_llm_agent_retry_recovers_transient_timeout():
    """首次 ReadTimeout、重试后成功 → ok=1、retry=1、llm_error=0。"""
    agent = LLMAgent(_FlakyProvider(fail_times=1), retry_backoff_seconds=0.0)
    out = asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    assert out["action_type"] == ActionType.NIGHT_KILL_NOMINATE.value
    assert agent.stats["ok"] == 1
    assert agent.stats["retry"] == 1
    assert agent.stats["llm_error"] == 0


# ===========================================================================
# S7 决策 trace 持久化 + canonicalize 拦截计量
# ===========================================================================

from stores.trace_store import InMemoryTraceStore  # noqa: E402


def test_llm_agent_emits_trace_on_ok_outcome():
    """注入 trace_store → ok 路径 append 一条 AgentDecisionTrace，含 prompt_version_id 等关键字段。"""
    store = InMemoryTraceStore()
    provider = FakeLLMProvider('{"action_type": "night_kill_nominate", "target": "P3"}')
    agent = LLMAgent(provider, trace_store=store)
    asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    traces = store.list_by_game("u")
    assert len(traces) == 1
    t = traces[0]
    assert t.agent_id == "P1"
    assert t.role == Role.WEREWOLF
    assert t.phase == Phase.NIGHT_WEREWOLF
    assert t.round == 1
    assert t.agent_version == "v0"
    assert t.prompt_version_id == "werewolf:v0_free_llm"
    assert t.decision_output["action_type"] == ActionType.NIGHT_KILL_NOMINATE.value
    assert t.decision_output["target"] == "P3"
    assert t.decision_quality_flags["outcome"] == "ok"
    assert t.decision_quality_flags["parse_error"] is False
    assert t.decision_quality_flags["llm_error"] is False
    assert t.decision_quality_flags["retry_count"] == 0


def test_llm_agent_trace_records_template_name_without_soul():
    """默认无 soul：trace 记 template_name，不出现 soul_id。"""
    store = InMemoryTraceStore()
    provider = FakeLLMProvider('{"action_type": "night_kill_nominate", "target": "P3"}')
    agent = LLMAgent(provider, trace_store=store)
    asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    t = store.list_by_game("u")[0]
    assert t.decision_quality_flags["template_name"] == "v0_free_llm"
    assert "soul_id" not in t.decision_quality_flags


def test_llm_agent_forwards_soul_id_and_records_it_in_trace():
    """soul_id 透传给默认 loader → 进 system prompt，且记进 trace.decision_quality_flags。"""
    store = InMemoryTraceStore()
    provider = FakeLLMProvider('{"action_type": "night_kill_nominate", "target": "P3"}')
    agent = LLMAgent(provider, trace_store=store, soul_id="cautious")
    asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    t = store.list_by_game("u")[0]
    assert t.decision_quality_flags["soul_id"] == "cautious"
    assert t.decision_quality_flags["template_name"] == "v0_free_llm"


def _hunter_shoot_ctx() -> AgentContext:
    players = [f"P{i}" for i in range(1, 10)]
    return AgentContext(
        game_id="u",
        agent_id="P1",
        role=Role.HUNTER,
        round=2,
        phase=Phase.HUNTER_SHOOT,
        visible_players=[VisiblePlayer(player_id=p, status=PlayerStatus.ALIVE) for p in players],
        allowed_actions=[ActionType.HUNTER_SHOOT],
    )


def test_llm_agent_no_selector_omits_strategy_flags():
    """默认无 strategy_selector：trace 不出现 strategy_snippet_ids（opt-in 不变）。"""
    store = InMemoryTraceStore()
    provider = FakeLLMProvider('{"action_type": "hunter_shoot", "target": "P3"}')
    agent = LLMAgent(provider, trace_store=store)
    asyncio.run(agent.act(_hunter_shoot_ctx().model_dump(mode="json")))

    t = store.list_by_game("u")[0]
    assert "strategy_snippet_ids" not in t.decision_quality_flags
    assert "activated_scene_tags" not in t.decision_quality_flags


def test_llm_agent_injects_strategy_and_records_in_trace():
    """带 selector + 触发场景：策略片段进 system，命中 id/tags 记进 trace。"""
    from agent_policy.advanced_strategy import StrategySelector

    captured: dict = {}

    class _CapturingProvider:
        async def generate(self, messages, model_config):
            captured["system"] = messages[0]["content"]
            return LLMResponse(raw_output='{"action_type": "hunter_shoot", "target": "P3"}')

    store = InMemoryTraceStore()
    agent = LLMAgent(_CapturingProvider(), trace_store=store, strategy_selector=StrategySelector())
    asyncio.run(agent.act(_hunter_shoot_ctx().model_dump(mode="json")))

    # 策略真的进了 system prompt
    assert "参考打法（非硬规则）" in captured["system"]
    assert "猎人开枪决策" in captured["system"]
    # trace 记了命中
    t = store.list_by_game("u")[0]
    assert t.decision_quality_flags["strategy_snippet_ids"] == ["hunter_shoot"]
    assert t.decision_quality_flags["activated_scene_tags"] == ["hunter_shoot"]


def test_llm_agent_selector_no_trigger_records_empty():
    """带 selector 但场景不触发：snippet_ids/tags 为空列表（区别于"没 selector"）。"""
    from agent_policy.advanced_strategy import StrategySelector

    store = InMemoryTraceStore()
    provider = FakeLLMProvider('{"action_type": "night_kill_nominate", "target": "P3"}')
    agent = LLMAgent(provider, trace_store=store, strategy_selector=StrategySelector())
    asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    t = store.list_by_game("u")[0]
    assert t.decision_quality_flags["strategy_snippet_ids"] == []
    assert t.decision_quality_flags["activated_scene_tags"] == []


def test_llm_agent_emits_trace_on_parse_error():
    """parse_error 路径仍 append trace，decision_output 含 fallback 标记。"""
    store = InMemoryTraceStore()
    agent = LLMAgent(FakeLLMProvider("非 JSON 胡言乱语"), trace_store=store)
    asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    traces = store.list_by_game("u")
    assert len(traces) == 1
    t = traces[0]
    assert t.decision_quality_flags["outcome"] == "parse_error"
    assert t.decision_quality_flags["parse_error"] is True
    assert t.decision_output["fallback"] is True


def test_llm_agent_emits_trace_on_llm_error():
    """llm_error 路径（重试耗尽）也 append trace，retry_count 反映本次决策的重试次数。"""
    store = InMemoryTraceStore()
    agent = LLMAgent(_BoomProvider(), retry_backoff_seconds=0.0, trace_store=store)
    asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))

    traces = store.list_by_game("u")
    assert len(traces) == 1
    t = traces[0]
    assert t.decision_quality_flags["outcome"] == "llm_error"
    assert t.decision_quality_flags["llm_error"] is True
    assert t.decision_quality_flags["retry_count"] == 2
    assert t.decision_output["fallback"] is True


def test_llm_agent_without_trace_store_keeps_legacy_behavior():
    """trace_store=None 时 act 完全不接触 store，向后兼容 A 的 run_v0_batch.py。"""
    # 无 store，act 不应崩，stats 正常累加
    agent = LLMAgent(FakeLLMProvider('{"action_type": "skip"}'))
    out = asyncio.run(agent.act(_werewolf_night_ctx().model_dump(mode="json")))
    assert out["action_type"] == ActionType.SKIP.value
    assert agent.stats["ok"] == 1


def test_llm_agent_canonicalize_stats_count_meta_ai_leak():
    """canonicalizer 命中 META_AI → stats.canonicalize_meta_ai +1，trace 含 canonicalize_triggered。"""
    store = InMemoryTraceStore()
    # 注入"作为一个 AI"元话术触发 META_AI 扫描；用 speak action 携带泄漏
    speak_ctx = AgentContext(
        game_id="u",
        agent_id="P1",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_DISCUSSION,
        visible_players=[VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)],
        allowed_actions=[ActionType.SPEAK],
    )
    raw = '{"action_type": "speak", "public_message": "作为一个AI助手，我推测 P3 是狼"}'
    agent = LLMAgent(FakeLLMProvider(raw), trace_store=store)
    asyncio.run(agent.act(speak_ctx.model_dump(mode="json")))

    assert agent.stats["canonicalize_meta_ai"] == 1
    assert agent.stats["canonicalize_cot_leak"] == 0
    traces = store.list_by_game("u")
    assert len(traces) == 1
    assert "meta_ai" in traces[0].decision_quality_flags["canonicalize_triggered"]


def test_llm_agent_trace_handles_seer_claim_result_dict():
    """REGRESSION 5/27：seer 跳预 + claim_result（ClaimResult pydantic model 不是 enum）
    在 _emit_trace 序列化时不能 .value——5 局 LLM batch 全在 round 2 DAY_DISCUSSION
    seer 发言时崩 AttributeError，根因就是这个。
    """
    store = InMemoryTraceStore()
    speak_ctx = AgentContext(
        game_id="u",
        agent_id="P4",
        role=Role.SEER,
        round=2,
        phase=Phase.DAY_DISCUSSION,
        visible_players=[VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)],
        allowed_actions=[ActionType.SPEAK],
    )
    raw = (
        '{"action_type": "speak", "public_message": "我是预言家昨晚查 P3 是狼",'
        ' "role_claim": "seer",'
        ' "claim_result": {"target": "P3", "claimed_alignment": "werewolf"}}'
    )
    agent = LLMAgent(FakeLLMProvider(raw), trace_store=store)
    out = asyncio.run(agent.act(speak_ctx.model_dump(mode="json")))

    # 关键：act 不抛 AttributeError → trace 也写入了
    assert out["action_type"] == ActionType.SPEAK.value
    assert agent.stats["ok"] == 1
    traces = store.list_by_game("u")
    assert len(traces) == 1
    # claim_result 应序列化为 dict {target, claimed_alignment}，不是 enum.value 字符串
    cr = traces[0].decision_output["claim_result"]
    assert cr == {"target": "P3", "claimed_alignment": "werewolf"}
    assert traces[0].decision_output["role_claim"] == "seer"


def test_llm_agent_canonicalize_stats_count_cot_leak():
    """canonicalizer 命中 COT_LEAK → stats.canonicalize_cot_leak +1。"""
    speak_ctx = AgentContext(
        game_id="u",
        agent_id="P1",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_DISCUSSION,
        visible_players=[VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)],
        allowed_actions=[ActionType.SPEAK],
    )
    raw = '{"action_type": "speak", "public_message": "我的思维链显示，P3 的行为可疑"}'
    agent = LLMAgent(FakeLLMProvider(raw))
    asyncio.run(agent.act(speak_ctx.model_dump(mode="json")))

    assert agent.stats["canonicalize_cot_leak"] == 1
    assert agent.stats["canonicalize_meta_ai"] == 0
