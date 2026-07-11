"""ContextAssembler 9 人端到端验证（Phase 3 S4）。

A 5/25 18:54 分配的"实现 9 人上下文与长记忆控制"任务的验证层：
确认 ContextWindowPolicy + SpeechSummarizer + ContextAssembler 在 9 人 D3
长场景下能压住 4000 token 预算 + Day 2+ 历史发言全走 Fact Stream。

覆盖：
- 9 人 D1 NIGHT 装配（visible_players=9、可见性过滤、budget 充裕）
- 9 人 D3 长场景（D1/D2 各 8 SPEECH + 当前 D3 8 SPEECH + 多轮死人 / vote / exile）
  - public_memory_summary 含 D1/D2 两个 FactStreamSummary
  - current_round_events 只含当前轮 SPEECH 原文
  - 没有历史 SPEECH 原文（HistoricalSpeechLeakError 兜底）
  - estimated tokens < 4000
- 9 人女巫读 max round 当夜刀口（多夜局 PrivateEvent.round 透传）
- 9 人狼 WOLF_NOMINATION roster 跨轮可见（round=1 持续可见到 D3+）
"""

from __future__ import annotations

import pytest

from contracts import (
    ClaimedAlignment,
    ClaimResult,
    EventType,
    Phase,
    PlayerStatus,
    Role,
    Visibility,
)

from context.context_assembler import ContextAssembler
from context.context_window_policy import ContextWindowPolicy
from stores.event_store import InMemoryEventStore
from tests.context.conftest import (
    FakeSessionProvider,
    make_9p_session,
    make_event,
    make_player,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assembler(session, events=None):
    provider = FakeSessionProvider(session)
    event_store = InMemoryEventStore()
    for ev in events or []:
        event_store.append(ev)
    return ContextAssembler(session_provider=provider, event_store=event_store)


def _speech(actor: str, round_num: int, text: str, *, role_claim=None,
            claim_result=None) -> object:
    """构造一条 SPEECH GameEvent。"""
    return make_event(
        EventType.SPEECH,
        round_num=round_num,
        phase=Phase.DAY_DISCUSSION,
        actor=actor,
        target=None,
        visibility=Visibility.PUBLIC,
        payload={
            "public_message": text,
            "role_claim": role_claim,
            "claim_result": claim_result.model_dump() if claim_result else None,
        },
        game_id="g009",
    )


def _vote(voter: str, target: str, round_num: int) -> object:
    return make_event(
        EventType.VOTE_CAST,
        round_num=round_num,
        phase=Phase.DAY_VOTE,
        actor=voter,
        target=target,
        visibility=Visibility.PUBLIC,
        game_id="g009",
    )


def _exile(target: str, round_num: int) -> object:
    return make_event(
        EventType.EXILE,
        round_num=round_num,
        phase=Phase.DAY_VOTE,
        actor=None,
        target=target,
        visibility=Visibility.PUBLIC,
        game_id="g009",
    )


def _night_kill(target: str, round_num: int) -> object:
    return make_event(
        EventType.DEATH_CONFIRMED,
        round_num=round_num,
        phase=Phase.DAY_ANNOUNCEMENT,
        actor=None,
        target=target,
        visibility=Visibility.PUBLIC,
        payload={"death_cause": "night_kill"},
        game_id="g009",
    )


# ---------------------------------------------------------------------------
# 9 人 D1 NIGHT 装配
# ---------------------------------------------------------------------------


class TestNinePlayerD1Night:
    def test_visible_players_count_9(self):
        session = make_9p_session(phase=Phase.NIGHT_WEREWOLF)
        assembler = _make_assembler(session)
        ctx = assembler.build_context("g009", "P1", Phase.NIGHT_WEREWOLF)
        assert len(ctx.visible_players) == 9
        assert {vp.player_id for vp in ctx.visible_players} == {
            f"P{i}" for i in range(1, 10)
        }

    def test_wolf_sees_three_teammates_in_private(self):
        """3 狼局：开局 WOLF_NOMINATION roster 含 P1/P2/P3，三人都能看到队友名单。"""
        session = make_9p_session(phase=Phase.NIGHT_WEREWOLF)
        roster = make_event(
            EventType.WOLF_NOMINATION,
            round_num=1,
            phase=Phase.NIGHT_WEREWOLF,
            actor=None,
            target=None,
            visibility=Visibility.PRIVATE_TO_WOLVES,
            payload={"teammates": ["P1", "P2", "P3"]},
            game_id="g009",
        )
        assembler = _make_assembler(session, events=[roster])
        for wolf_pid in ["P1", "P2", "P3"]:
            ctx = assembler.build_context("g009", wolf_pid, Phase.NIGHT_WEREWOLF)
            assert len(ctx.private_events) == 1
            assert ctx.private_events[0].teammates == ["P1", "P2", "P3"]
            assert ctx.private_events[0].round == 1

    def test_villager_sees_no_wolf_roster(self):
        session = make_9p_session(phase=Phase.NIGHT_WEREWOLF)
        roster = make_event(
            EventType.WOLF_NOMINATION,
            round_num=1,
            visibility=Visibility.PRIVATE_TO_WOLVES,
            payload={"teammates": ["P1", "P2", "P3"]},
            game_id="g009",
        )
        assembler = _make_assembler(session, events=[roster])
        ctx = assembler.build_context("g009", "P7", Phase.NIGHT_WEREWOLF)
        assert ctx.private_events == []


# ---------------------------------------------------------------------------
# 9 人 D3 长场景：核心验证 token 预算 + Fact Stream 压缩
# ---------------------------------------------------------------------------


class TestNinePlayerD3LongHistory:
    """构造 D1/D2 各 8 SPEECH + 死人 + 投票 + 流放，D3 进入 DAY_DISCUSSION 已 4 SPEECH。

    9 人局每天最多 9 人发言，3 轮下来累计 24 SPEECH + 死亡/投票事件 ≈ 50+ events。
    验证 ContextAssembler 能把历史 D1/D2 发言压成 Fact Stream，
    当前 D3 SPEECH 走原文，且 token 估算 < 4000。
    """

    def _build_three_day_events(self):
        """构造 D1/D2 完整 + D3 进行中。"""
        events = []
        seer_claim = ClaimResult(
            target="P1", claimed_alignment=ClaimedAlignment.WEREWOLF
        )

        # --- D1 ---
        events.append(_night_kill("P9", round_num=1))  # P9 villager 夜里死
        # D1 发言 8 人（P9 已死）
        events.append(
            _speech("P4", 1, "我是预言家，昨夜查 P1 是狼",
                    role_claim=Role.SEER, claim_result=seer_claim)
        )
        events.append(_speech("P1", 1, "他骗你们的，我不是狼"))
        events.append(_speech("P2", 1, "P4 看着不像预言家"))
        events.append(_speech("P5", 1, "我是女巫，相信 P4"))
        events.append(_speech("P6", 1, "我是猎人，先观望"))
        events.append(_speech("P7", 1, "我是平民，跟预言家"))
        events.append(_speech("P8", 1, "同跟 P4"))
        events.append(_speech("P3", 1, "P4 单跳，可信"))
        # D1 投票：大家投 P1，P1 出局
        for voter in ["P4", "P5", "P6", "P7", "P8", "P3"]:
            events.append(_vote(voter, "P1", round_num=1))
        events.append(_vote("P1", "P4", round_num=1))
        events.append(_vote("P2", "P4", round_num=1))
        events.append(_exile("P1", round_num=1))

        # --- D2 ---
        events.append(_night_kill("P7", round_num=2))  # P7 villager 死
        # D2 发言 7 人（P9/P1/P7 已死）
        seer_claim_d2 = ClaimResult(
            target="P2", claimed_alignment=ClaimedAlignment.WEREWOLF
        )
        events.append(
            _speech("P4", 2, "昨夜查 P2 是狼",
                    role_claim=Role.SEER, claim_result=seer_claim_d2)
        )
        events.append(_speech("P2", 2, "P4 是悍跳，我才是预言家"))
        events.append(_speech("P5", 2, "我相信 P4 是真预言家"))
        events.append(_speech("P6", 2, "继续观望"))
        events.append(_speech("P8", 2, "跟预言家投 P2"))
        events.append(_speech("P3", 2, "P4 验人准确"))
        # D2 投票：大家投 P2
        for voter in ["P4", "P5", "P6", "P8", "P3"]:
            events.append(_vote(voter, "P2", round_num=2))
        events.append(_vote("P2", "P4", round_num=2))
        events.append(_exile("P2", round_num=2))

        # --- D3（进行中）---
        events.append(_night_kill("P5", round_num=3))  # 女巫 P5 也死了
        # D3 已 4 SPEECH（P3/P4/P6/P8 还活着）
        seer_claim_d3 = ClaimResult(
            target="P3", claimed_alignment=ClaimedAlignment.WEREWOLF
        )
        events.append(
            _speech("P4", 3, "昨夜查 P3 是狼，最后一只狼",
                    role_claim=Role.SEER, claim_result=seer_claim_d3)
        )
        events.append(_speech("P3", 3, "P4 假预言家，我才是真的"))
        events.append(_speech("P6", 3, "我猎人，等下投 P3 准备开枪"))
        events.append(_speech("P8", 3, "投 P3"))
        return events

    def test_d3_long_history_compresses_to_fact_streams(self):
        """9 人 D3：D1/D2 历史发言被压成 FactStreamSummary，不进 raw events。"""
        # D3 进行中：P1/P2/P5/P7/P9 已死，P3/P4/P6/P8 存活
        dead = {
            "P1": make_player(Role.WEREWOLF, PlayerStatus.DEAD),
            "P2": make_player(Role.WEREWOLF, PlayerStatus.DEAD),
            "P5": make_player(Role.WITCH, PlayerStatus.DEAD),
            "P7": make_player(Role.VILLAGER, PlayerStatus.DEAD),
            "P9": make_player(Role.VILLAGER, PlayerStatus.DEAD),
        }
        session = make_9p_session(
            round_num=3,
            phase=Phase.DAY_DISCUSSION,
            overrides=dead,
        )
        events = self._build_three_day_events()
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g009", "P4", Phase.DAY_DISCUSSION)

        # 1) 当前 round 含 D3 4 SPEECH 原文 + NIGHT_KILL_ANNOUNCED
        d3_speeches = [
            ev for ev in ctx.current_round_events
            if ev.event_type == EventType.SPEECH and ev.round == 3
        ]
        assert len(d3_speeches) == 4

        # 2) Day 2+ 历史 SPEECH 原文不在 current_round_events / recent_public_events
        all_speech_events = [
            ev for ev in ctx.recent_public_events
            if ev.event_type == EventType.SPEECH
        ]
        assert all(ev.round == 3 for ev in all_speech_events), \
            f"历史 SPEECH 原文泄露：{[ev.round for ev in all_speech_events if ev.round < 3]}"

        # 3) public_memory_summary 至少含 D1 / D2 两个 FactStream
        rounds_summarized = {fs["round"] for fs in ctx.public_memory_summary}
        assert 1 in rounds_summarized
        assert 2 in rounds_summarized
        assert 3 not in rounds_summarized  # 当前轮不压缩

    def test_d3_token_estimate_within_budget(self):
        """9 人 D3 + D1/D2 历史完整：估算 token 不能超 4000。"""
        dead = {
            "P1": make_player(Role.WEREWOLF, PlayerStatus.DEAD),
            "P2": make_player(Role.WEREWOLF, PlayerStatus.DEAD),
            "P5": make_player(Role.WITCH, PlayerStatus.DEAD),
            "P7": make_player(Role.VILLAGER, PlayerStatus.DEAD),
            "P9": make_player(Role.VILLAGER, PlayerStatus.DEAD),
        }
        session = make_9p_session(
            round_num=3, phase=Phase.DAY_DISCUSSION, overrides=dead,
        )
        events = self._build_three_day_events()
        assembler = _make_assembler(session, events=events)
        # 对每个存活玩家装配 context，估算 token，全部 < 4000
        policy = ContextWindowPolicy()
        for pid in ["P3", "P4", "P6", "P8"]:
            ctx = assembler.build_context("g009", pid, Phase.DAY_DISCUSSION)
            est = policy.estimate_tokens(ctx)
            assert est < 4000, f"{pid} D3 ctx token {est} >= 4000"

    def test_d3_fact_stream_contains_key_facts(self):
        """D1/D2 关键事实 (死亡 / claim seer / 查杀 / 投票 / 流放) 进入 Fact Stream。"""
        dead = {
            "P1": make_player(Role.WEREWOLF, PlayerStatus.DEAD),
            "P2": make_player(Role.WEREWOLF, PlayerStatus.DEAD),
            "P5": make_player(Role.WITCH, PlayerStatus.DEAD),
            "P7": make_player(Role.VILLAGER, PlayerStatus.DEAD),
            "P9": make_player(Role.VILLAGER, PlayerStatus.DEAD),
        }
        session = make_9p_session(
            round_num=3, phase=Phase.DAY_DISCUSSION, overrides=dead,
        )
        events = self._build_three_day_events()
        assembler = _make_assembler(session, events=events)
        ctx = assembler.build_context("g009", "P4", Phase.DAY_DISCUSSION)

        # 把所有 Fact Stream 字符串拼起来便于 grep 关键信号
        all_facts = " ".join(
            fact
            for fs in ctx.public_memory_summary
            for fact in fs["facts"]
        )
        # 死亡
        assert "P9 confirmed dead" in all_facts
        assert "P7 confirmed dead" in all_facts
        # P4 跳预言家 + 查杀
        assert "self_claim seer" in all_facts.lower() \
            or "self_claim Seer" in all_facts
        # P1 / P2 流放
        assert "P1 executed" in all_facts
        assert "P2 executed" in all_facts
        # 投票
        assert "P4->P1" in all_facts or "->P1" in all_facts


# ---------------------------------------------------------------------------
# 9 人多夜女巫 max round 锁当夜刀口
# ---------------------------------------------------------------------------


class TestWitchMaxRoundKillTarget:
    def test_witch_takes_max_round_kill_target(self):
        """女巫第 3 夜：private_events 含 D1/D2/D3 三条 WITCH_KILL_TARGET_INFO，
        消费方按 round 取 max 才能锁定当夜刀口（A 5/24 23:59 拍板的契约语义）。
        """
        session = make_9p_session(
            round_num=3, phase=Phase.NIGHT_WITCH,
        )
        kill_info_d1 = make_event(
            EventType.WITCH_KILL_TARGET_INFO,
            round_num=1,
            phase=Phase.NIGHT_WITCH,
            actor=None,
            target="P9",
            visibility=Visibility.PRIVATE_TO_WITCH,
            game_id="g009",
        )
        kill_info_d2 = make_event(
            EventType.WITCH_KILL_TARGET_INFO,
            round_num=2,
            phase=Phase.NIGHT_WITCH,
            actor=None,
            target="P7",
            visibility=Visibility.PRIVATE_TO_WITCH,
            game_id="g009",
        )
        kill_info_d3 = make_event(
            EventType.WITCH_KILL_TARGET_INFO,
            round_num=3,
            phase=Phase.NIGHT_WITCH,
            actor=None,
            target="P8",
            visibility=Visibility.PRIVATE_TO_WITCH,
            game_id="g009",
        )
        assembler = _make_assembler(
            session, events=[kill_info_d1, kill_info_d2, kill_info_d3]
        )
        ctx = assembler.build_context("g009", "P5", Phase.NIGHT_WITCH)

        kill_infos = [
            pe for pe in ctx.private_events
            if pe.event_type == EventType.WITCH_KILL_TARGET_INFO
        ]
        assert len(kill_infos) == 3
        # 消费方语义：取 max round
        latest = max(kill_infos, key=lambda pe: pe.round or 0)
        assert latest.round == 3
        assert latest.target == "P8"


# ---------------------------------------------------------------------------
# 9 人预言家全量查验史（忽略 round 读所有）
# ---------------------------------------------------------------------------


class TestSeerFullHistory:
    def test_seer_sees_all_check_history_across_rounds(self):
        """预言家：private_events 包含 D1/D2/D3 三条 SEER_CHECK_RESULT，全量保留，
        消费方忽略 round 直接读（_known_check_results 依赖此）。
        """
        session = make_9p_session(
            round_num=3, phase=Phase.DAY_DISCUSSION,
        )
        check_d1 = make_event(
            EventType.SEER_CHECK_RESULT,
            round_num=1, phase=Phase.NIGHT_SEER,
            actor="P4", target="P1",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "werewolf"},
            game_id="g009",
        )
        check_d2 = make_event(
            EventType.SEER_CHECK_RESULT,
            round_num=2, phase=Phase.NIGHT_SEER,
            actor="P4", target="P2",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "werewolf"},
            game_id="g009",
        )
        check_d3 = make_event(
            EventType.SEER_CHECK_RESULT,
            round_num=3, phase=Phase.NIGHT_SEER,
            actor="P4", target="P3",
            visibility=Visibility.PRIVATE_TO_SEER,
            payload={"result": "werewolf"},
            game_id="g009",
        )
        assembler = _make_assembler(
            session, events=[check_d1, check_d2, check_d3]
        )
        ctx = assembler.build_context("g009", "P4", Phase.DAY_DISCUSSION)
        checks = [
            pe for pe in ctx.private_events
            if pe.event_type == EventType.SEER_CHECK_RESULT
        ]
        assert len(checks) == 3
        targets = {pe.target: pe.round for pe in checks}
        assert targets == {"P1": 1, "P2": 2, "P3": 3}
