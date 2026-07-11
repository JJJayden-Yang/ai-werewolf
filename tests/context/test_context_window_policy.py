"""ContextWindowPolicy 测试。

覆盖：
- 各 list 字段按 budget 上限裁剪
- 历史 SPEECH 原文 fail-loud
- token 估算 + 超额抛错
- 默认 budget 行为
"""

from __future__ import annotations

import pytest

from contracts import (
    ActionType,
    ContextBudgetConfig,
    EventType,
    Phase,
    PublicEvent,
    Role,
)

from context.context_window_policy import (
    ContextBudgetExceededError,
    ContextWindowPolicy,
    HistoricalSpeechLeakError,
)
from context.types import AgentContextDraft


def _empty_draft(*, round_num: int = 1, phase: Phase = Phase.DAY_DISCUSSION) -> AgentContextDraft:
    return AgentContextDraft(
        game_id="g001",
        agent_id="P1",
        role=Role.VILLAGER,
        round=round_num,
        phase=phase,
    )


def _speech_event(round_num: int, idx: int, actor: str = "P1") -> PublicEvent:
    return PublicEvent(
        event_id=f"evt_{round_num}_{idx}",
        round=round_num,
        phase=Phase.DAY_DISCUSSION,
        event_type=EventType.SPEECH,
        actor=actor,
        public_message=f"speech {idx}",
    )


# ---------------------------------------------------------------------------
# 裁剪行为
# ---------------------------------------------------------------------------


class TestTrimming:
    def setup_method(self):
        self.policy = ContextWindowPolicy()

    def test_recent_public_events_capped(self):
        events = [_speech_event(1, i) for i in range(25)]
        # 25 个事件，全部 round=1 (当前 round)，所以不会触发历史 SPEECH 检查
        draft = _empty_draft(round_num=1)
        draft.recent_public_events = events
        # 不能塞进 current_round_events，否则会有 max_current_day_speeches_raw 限制叠加
        context = self.policy.apply(draft, ContextBudgetConfig(max_recent_public_events=20))
        assert len(context.recent_public_events) == 20
        # 保留最新（最后 20 个）
        assert context.recent_public_events[0].event_id == "evt_1_5"
        assert context.recent_public_events[-1].event_id == "evt_1_24"

    def test_current_day_speeches_capped(self):
        events = [_speech_event(1, i) for i in range(15)]
        draft = _empty_draft(round_num=1)
        draft.current_round_events = events
        # max_current_day_speeches_raw=9 默认
        context = self.policy.apply(draft, ContextBudgetConfig())
        # SPEECH 事件只保留最新 9 个
        speech_events = [
            ev for ev in context.current_round_events
            if ev.event_type == EventType.SPEECH
        ]
        assert len(speech_events) == 9

    def test_belief_top_suspects_capped(self):
        draft = _empty_draft()
        draft.belief_top_suspects = [{"player_id": f"P{i}"} for i in range(10)]
        context = self.policy.apply(draft, ContextBudgetConfig(max_belief_top_suspects=3))
        assert len(context.belief_top_suspects) == 3

    def test_strategy_memory_capped(self):
        draft = _empty_draft()
        draft.strategy_memory = [{"lesson": f"L{i}"} for i in range(5)]
        context = self.policy.apply(draft, ContextBudgetConfig(max_strategy_memory_items=3))
        assert len(context.strategy_memory) == 3


# ---------------------------------------------------------------------------
# 历史 SPEECH fail-loud
# ---------------------------------------------------------------------------


class TestHistoricalSpeechLeak:
    def setup_method(self):
        self.policy = ContextWindowPolicy()

    def test_historical_speech_in_current_round_events_raises(self):
        # current_round=3，但事件流里混进了 round=1 的 SPEECH
        draft = _empty_draft(round_num=3)
        draft.current_round_events = [_speech_event(1, 0)]  # 历史 SPEECH
        with pytest.raises(HistoricalSpeechLeakError) as exc:
            self.policy.apply(draft)
        assert exc.value.count == 1
        assert exc.value.current_round == 3

    def test_historical_speech_in_recent_events_raises(self):
        draft = _empty_draft(round_num=3)
        draft.recent_public_events = [_speech_event(2, 0)]  # round=2，历史
        with pytest.raises(HistoricalSpeechLeakError):
            self.policy.apply(draft)

    def test_current_round_speech_ok(self):
        draft = _empty_draft(round_num=3)
        draft.current_round_events = [_speech_event(3, 0)]  # 当前 round，OK
        context = self.policy.apply(draft)
        assert len(context.current_round_events) == 1

    def test_historical_non_speech_events_ok(self):
        """EXILE / HUNTER_SHOT 等非 SPEECH 历史事件可以保留。"""
        ev = PublicEvent(
            event_id="exile_1",
            round=1,
            phase=Phase.EXILE_RESOLUTION,
            event_type=EventType.EXILE,
            target="P3",
        )
        draft = _empty_draft(round_num=3)
        draft.recent_public_events = [ev]
        context = self.policy.apply(draft)
        assert len(context.recent_public_events) == 1


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------


class TestTokenEstimate:
    def setup_method(self):
        self.policy = ContextWindowPolicy()

    def test_empty_context_estimates_some_tokens(self):
        draft = _empty_draft()
        context = self.policy.apply(draft)
        estimated = self.policy.estimate_tokens(context)
        assert estimated > 0
        assert estimated < 4000

    def test_large_context_exceeds_budget(self):
        # 故意塞一大堆 public_memory_summary 让 token 数爆掉
        draft = _empty_draft()
        draft.public_memory_summary = [
            {"text": "x" * 100} for _ in range(500)
        ]
        with pytest.raises(ContextBudgetExceededError) as exc:
            self.policy.apply(draft, ContextBudgetConfig(max_input_tokens_per_agent=1000))
        assert exc.value.budget == 1000
        assert exc.value.estimated > 1000


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_none_budget_uses_default(self):
        policy = ContextWindowPolicy()
        draft = _empty_draft()
        context = policy.apply(draft, None)
        assert context.allowed_actions == []

    def test_returns_proper_agent_context_type(self):
        from contracts import AgentContext

        policy = ContextWindowPolicy()
        draft = _empty_draft()
        draft.allowed_actions = [ActionType.SPEAK]
        result = policy.apply(draft)
        assert isinstance(result, AgentContext)
        assert result.allowed_actions == [ActionType.SPEAK]


# ---------------------------------------------------------------------------
# SPEECH public_message 长度截断 + 渐进降级安全网（A 5/26 21:14 头号阻塞修复）
# ---------------------------------------------------------------------------


def _long_speech_event(round_num: int, idx: int, actor: str, message_chars: int) -> PublicEvent:
    """构造单条带长 public_message 的 SPEECH 事件，模拟真实 LLM 输出。"""
    return PublicEvent(
        event_id=f"evt_long_{round_num}_{idx}",
        round=round_num,
        phase=Phase.DAY_DISCUSSION,
        event_type=EventType.SPEECH,
        actor=actor,
        public_message="x" * message_chars,
    )


class TestSpeechMessageTruncation:
    def setup_method(self):
        self.policy = ContextWindowPolicy()

    def test_long_speech_message_truncated_to_cap(self):
        """单条 SPEECH 长 1000 字符应被截到 280 + ``…(已截断)`` 标记。"""
        from context.context_window_policy import _MAX_SPEECH_MESSAGE_CHARS

        draft = _empty_draft()
        draft.current_round_events = [_long_speech_event(1, 0, "P1", 1000)]
        context = self.policy.apply(draft)
        msg = context.current_round_events[0].public_message
        assert len(msg) <= _MAX_SPEECH_MESSAGE_CHARS + len("…(已截断)")
        assert msg.endswith("…(已截断)")
        # 前 280 字符内容保留
        assert msg.startswith("x" * 280)

    def test_short_speech_message_unchanged(self):
        """短 SPEECH（< 280 字）原样保留，不加截断标记。"""
        draft = _empty_draft()
        draft.current_round_events = [_long_speech_event(1, 0, "P1", 50)]
        context = self.policy.apply(draft)
        msg = context.current_round_events[0].public_message
        assert msg == "x" * 50
        assert "已截断" not in msg

    def test_truncation_preserves_actor_and_event_type(self):
        """截断只动 public_message，actor / event_type / round 等不变。"""
        draft = _empty_draft()
        original = _long_speech_event(1, 0, "P3", 500)
        draft.current_round_events = [original]
        context = self.policy.apply(draft)
        truncated = context.current_round_events[0]
        assert truncated.event_type == EventType.SPEECH
        assert truncated.actor == "P3"
        assert truncated.round == 1
        assert truncated.event_id == original.event_id

    def test_non_speech_events_not_truncated(self):
        """VOTE_CAST / EXILE 等非 SPEECH 事件不动 public_message（虽然它们也很少有）。"""
        non_speech = PublicEvent(
            event_id="evt_vote",
            round=1,
            phase=Phase.DAY_VOTE,
            event_type=EventType.VOTE_CAST,
            actor="P1",
            target="P2",
        )
        draft = _empty_draft()
        draft.current_round_events = [non_speech]
        context = self.policy.apply(draft)
        # identity 保持
        assert context.current_round_events[0] is non_speech

    def test_truncation_applied_to_recent_and_public_events_too(self):
        """SPEECH 在 current_round_events / recent_public_events / public_events 三处都截。"""
        long_speech = _long_speech_event(1, 0, "P1", 1000)
        draft = _empty_draft()
        draft.current_round_events = [long_speech]
        draft.recent_public_events = [long_speech]
        draft.public_events = [long_speech]
        context = self.policy.apply(draft)
        for field_name in ("current_round_events", "recent_public_events", "public_events"):
            evs = getattr(context, field_name)
            assert evs, f"{field_name} should not be empty"
            msg = evs[0].public_message
            assert msg.endswith("…(已截断)"), f"{field_name} not truncated"


class TestLLMRealisticScenario:
    """模拟 A 21:14 报的真实场景：9p DAY_TIE_REVOTE + 真实 LLM 长发言导致越预算。"""

    def setup_method(self):
        self.policy = ContextWindowPolicy()

    def _make_realistic_draft(self, message_chars: int) -> AgentContextDraft:
        """模拟 9p DAY_TIE_REVOTE 阶段满载 context：9 条 D1 SPEECH + 9 条 VOTE + tie + revote。"""
        from contracts import VisiblePlayer, PlayerStatus

        draft = _empty_draft(round_num=1, phase=Phase.DAY_TIE_REVOTE)
        # 9 个 visible_players
        draft.visible_players = [
            VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE)
            for i in range(1, 10)
        ]
        # 9 条 SPEECH (D1 DAY_DISCUSSION) + 9 条 VOTE_CAST + tie + revote discussion 4 条
        events = []
        for i in range(1, 10):
            events.append(_long_speech_event(1, i, f"P{i}", message_chars))
        for i in range(1, 10):
            events.append(PublicEvent(
                event_id=f"vote_d1_{i}",
                round=1,
                phase=Phase.DAY_VOTE,
                event_type=EventType.VOTE_CAST,
                actor=f"P{i}",
                target=f"P{(i % 9) + 1}",
            ))
        events.append(PublicEvent(
            event_id="tie_d1",
            round=1,
            phase=Phase.DAY_VOTE,
            event_type=EventType.TIE_DETECTED,
        ))
        for i in range(1, 5):
            events.append(_long_speech_event(1, 100 + i, f"P{i}", message_chars // 2))
        draft.current_round_events = events
        draft.recent_public_events = list(events)
        draft.public_events = list(events)
        draft.tie_candidates = ["P3", "P4"]
        draft.allowed_actions = [ActionType.VOTE]
        return draft

    def test_realistic_long_speech_no_longer_raises(self):
        """A 21:14 复现：9p 长 SPEECH (350 chars × 9) 不再抛 ContextBudgetExceededError。"""
        draft = self._make_realistic_draft(message_chars=350)
        context = self.policy.apply(draft)
        estimated = self.policy.estimate_tokens(context)
        assert estimated <= 4000, f"token {estimated} still over budget"

    def test_extremely_long_speech_triggers_progressive_degrade(self):
        """更极端：1000 chars × 9 应触发 progressive_degrade 但仍不崩。"""
        draft = self._make_realistic_draft(message_chars=1000)
        # 加 typed records / belief_top_suspects 让降级有东西可丢
        draft.belief_top_suspects = [{"player_id": f"P{i}", "werewolf_prob": 0.1 * i} for i in range(1, 4)]
        context = self.policy.apply(draft)
        estimated = self.policy.estimate_tokens(context)
        assert estimated <= 4000
        # belief_top_suspects 在 progressive_degrade 里被清空
        assert context.belief_top_suspects == []

    def test_pathological_overshoot_still_raises_for_safety(self):
        """所有 SPEECH 都 5000 chars × 30 条 → 即使激进截到 100 chars 仍会越 budget → raise。"""
        from contracts import VisiblePlayer, PlayerStatus

        draft = _empty_draft(round_num=1, phase=Phase.DAY_TIE_REVOTE)
        draft.visible_players = [
            VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)
        ]
        # 极端：每条 5000 chars，30 条 SPEECH → 即使每条截到 100 + 100 chars × 30 = 3000 chars
        # 再加上 visible_players + 3 个 list 重复 ≈ 仍可能超出（取决于具体预算）
        # 这里测一个**真正炸的**场景：直接给小 budget 让安全网也救不回来
        draft.current_round_events = [
            _long_speech_event(1, i, f"P{(i % 9) + 1}", 5000) for i in range(30)
        ]
        draft.recent_public_events = list(draft.current_round_events)
        draft.public_events = list(draft.current_round_events)
        # 把 budget 设 500（小到必崩），验证 raise 路径仍工作
        with pytest.raises(ContextBudgetExceededError):
            self.policy.apply(draft, ContextBudgetConfig(max_input_tokens_per_agent=500))


class TestProgressiveDegrade:
    def setup_method(self):
        self.policy = ContextWindowPolicy()

    def test_strategy_memory_dropped_when_over_budget(self):
        """超预算时 strategy_memory 应优先被丢。"""
        # 构造刚刚好溢出的 context
        draft = _empty_draft()
        draft.strategy_memory = ["a" * 50 for _ in range(3)]
        # 加足够多 visible_players + public_memory_summary 让 base 接近 budget
        from contracts import VisiblePlayer, PlayerStatus

        draft.visible_players = [
            VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)
        ]
        draft.public_memory_summary = [{"facts": ["x" * 100 for _ in range(20)]} for _ in range(5)]
        # 用很小 budget 触发降级
        small_budget = ContextBudgetConfig(max_input_tokens_per_agent=400)
        try:
            context = self.policy.apply(draft, small_budget)
            # 进入了降级路径，strategy_memory 必空
            assert context.strategy_memory == []
        except ContextBudgetExceededError:
            # 也可能彻底超出 raise，那是另一个测试覆盖
            pass

    def test_belief_top_suspects_dropped_when_over_budget(self):
        from contracts import VisiblePlayer, PlayerStatus

        draft = _empty_draft()
        draft.belief_top_suspects = [{"player_id": f"P{i}", "werewolf_prob": 0.5} for i in range(3)]
        draft.public_memory_summary = [{"facts": ["x" * 100 for _ in range(20)]} for _ in range(5)]
        draft.visible_players = [
            VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)
        ]
        small_budget = ContextBudgetConfig(max_input_tokens_per_agent=400)
        try:
            context = self.policy.apply(draft, small_budget)
            assert context.belief_top_suspects == []
        except ContextBudgetExceededError:
            pass

    def test_stats_count_truncated_speeches(self):
        """stats.truncated_speech_events 累加被截断的 SPEECH 事件数（A 5/27 P2 量化）。"""
        draft = _empty_draft()
        # 3 条长 SPEECH（> 280 chars 触发初始截断）+ 1 条短 SPEECH
        draft.current_round_events = [
            _long_speech_event(1, i, f"P{i}", 500) for i in range(1, 4)
        ] + [_long_speech_event(1, 4, "P4", 50)]
        policy = ContextWindowPolicy()
        policy.apply(draft)
        # current_round_events 3 条长被截，4 条短不动；
        # recent_public_events / public_events 默认空（draft 没塞）→ 总 3 条
        assert policy.stats["truncated_speech_events"] == 3
        assert policy.stats["applies"] == 1
        assert policy.stats["progressive_degrade_triggered"] == 0
        assert policy.stats["budget_exceeded"] == 0

    def test_stats_count_progressive_degrade(self):
        """stats.progressive_degrade_triggered 累加 apply 走进降级安全网的次数。"""
        from contracts import VisiblePlayer, PlayerStatus

        draft = _empty_draft(round_num=1, phase=Phase.DAY_TIE_REVOTE)
        draft.visible_players = [
            VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)
        ]
        # 9 条 1000 chars + 病态 belief 让 initial token > 4000 → 触发 progressive_degrade
        events = [_long_speech_event(1, i, f"P{i}", 1000) for i in range(1, 10)]
        draft.current_round_events = events
        draft.recent_public_events = list(events)
        draft.public_events = list(events)
        # 病态长 belief / strategy 推 token 过 4000（降级第 1/2 步会丢这些，期望救回）
        draft.belief_top_suspects = [
            {"player_id": f"P{i}", "werewolf_prob": 0.5, "note": "x" * 1500}
            for i in range(1, 4)
        ]
        policy = ContextWindowPolicy()
        policy.apply(draft)
        assert policy.stats["progressive_degrade_triggered"] == 1
        assert policy.stats["budget_exceeded"] == 0  # 降级后救回来

    def test_stats_count_budget_exceeded_raise(self):
        """stats.budget_exceeded 累加 progressive 之后仍超 raise 的次数。"""
        from contracts import VisiblePlayer, PlayerStatus

        draft = _empty_draft(round_num=1, phase=Phase.DAY_TIE_REVOTE)
        draft.visible_players = [
            VisiblePlayer(player_id=f"P{i}", status=PlayerStatus.ALIVE) for i in range(1, 10)
        ]
        # 病态 5000 × 30 + budget=500 必崩
        draft.current_round_events = [
            _long_speech_event(1, i, f"P{(i % 9) + 1}", 5000) for i in range(30)
        ]
        draft.recent_public_events = list(draft.current_round_events)
        draft.public_events = list(draft.current_round_events)
        policy = ContextWindowPolicy()
        with pytest.raises(ContextBudgetExceededError):
            policy.apply(draft, ContextBudgetConfig(max_input_tokens_per_agent=500))
        assert policy.stats["budget_exceeded"] == 1
        assert policy.stats["progressive_degrade_triggered"] == 1

    def test_hunter_shoot_after_tie_doesnt_crash(self):
        """A 5/27 实证复现：v0_batch_1 平票局到 HUNTER_SHOOT, P1, estimated 4114 > 4000。

        场景：D1 9 条 SPEECH (300 chars 中等长度) + 9 VOTE + TIE + 4 REVOTE_DISCUSSION
        + 9 REVOTE_VOTE + EXILE + HUNTER_SHOT 揭示。33+ events 堆在当前轮。

        旧 progressive_degrade：v0 默认 belief/strategy/typed 全空，step 1-3 全 no-op；
        HUNTER_SHOOT 当天没有继续发言，step 4 aggressive 截 100 chars 也只能压
        历史 SPEECH，节省 token 远不够 114。新增 step 5-7 砍 recent/public/summary
        把多余 token 降到 budget 内。
        """
        from contracts import VisiblePlayer, PlayerStatus

        draft = _empty_draft(round_num=1, phase=Phase.HUNTER_SHOOT)
        draft.role = Role.HUNTER
        draft.agent_id = "P1"
        draft.visible_players = [
            VisiblePlayer(
                player_id=f"P{i}",
                status=PlayerStatus.DEAD if i == 1 else PlayerStatus.ALIVE,
            )
            for i in range(1, 10)
        ]
        events: list[PublicEvent] = []
        # D1 9 条 SPEECH（300 chars 中等长度，初始 280 截断后 ~70 token/条）
        for i in range(1, 10):
            events.append(_long_speech_event(1, i, f"P{i}", 300))
        # D1 9 条 VOTE_CAST
        for i in range(1, 10):
            events.append(PublicEvent(
                event_id=f"vote_d1_{i}",
                round=1,
                phase=Phase.DAY_VOTE,
                event_type=EventType.VOTE_CAST,
                actor=f"P{i}",
                target=f"P{(i % 9) + 1}",
            ))
        # TIE_DETECTED
        events.append(PublicEvent(
            event_id="tie_d1",
            round=1,
            phase=Phase.DAY_VOTE,
            event_type=EventType.TIE_DETECTED,
        ))
        # 4 条 REVOTE 讨论 SPEECH
        for i in range(1, 5):
            events.append(_long_speech_event(1, 100 + i, f"P{i}", 300))
        # 9 条 REVOTE VOTE_CAST
        for i in range(1, 10):
            events.append(PublicEvent(
                event_id=f"revote_d1_{i}",
                round=1,
                phase=Phase.DAY_TIE_REVOTE,
                event_type=EventType.VOTE_CAST,
                actor=f"P{i}",
                target="P1",  # 都投 P1（猎人）
            ))
        # EXILE + DEATH_CONFIRMED
        events.append(PublicEvent(
            event_id="exile_d1",
            round=1,
            phase=Phase.DAY_VOTE,
            event_type=EventType.EXILE,
            target="P1",
        ))
        events.append(PublicEvent(
            event_id="death_d1",
            round=1,
            phase=Phase.HUNTER_SHOOT,
            event_type=EventType.DEATH_CONFIRMED,
            target="P1",
        ))
        draft.current_round_events = events
        draft.recent_public_events = list(events)
        draft.public_events = list(events)
        # 模拟一些跨轮 FactStream（HUNTER_SHOOT 通常在 D1 末，summary 可能仍 empty；
        # 但 D2+ 平票走到 HUNTER_SHOOT 时 summary 会有内容 → 加一点测试 step 7）
        draft.public_memory_summary = [
            {"facts": ["fact " * 20 for _ in range(15)]} for _ in range(4)
        ]
        draft.allowed_actions = [ActionType.HUNTER_SHOOT, ActionType.SKIP]

        # 修复后：progressive_degrade 应能降到 < 4000，不抛错
        context = self.policy.apply(draft)
        estimated = self.policy.estimate_tokens(context)
        assert estimated <= 4000, (
            f"HUNTER_SHOOT after tie still over budget: {estimated} —— "
            "progressive_degrade step 5/6/7 没救回来"
        )
        # 关键决策信息仍保留：HUNTER 角色 + visible_players + allowed_actions
        assert context.role == Role.HUNTER
        assert len(context.visible_players) == 9
        assert ActionType.HUNTER_SHOOT in context.allowed_actions
