"""SpeechSummarizer —— Task C9。Fact Stream 生成器。

Day 2 及以后**禁止**把历史发言原文放进 prompt。本类把过往 ``public_events``
压成客观短句 Fact Stream，由 ``ContextAssembler`` 注入到
``AgentContext.public_memory_summary``。

Fact Stream 格式（见 Interface_v2_1 §4.4）::

    D1 Daybreak: P4 died by night_kill.
    D1 Speech: P1 self_claim Seer, check P3 Wolf; P2 self_claim Villager.
    D1 Vote: P1->P3; P2->P3; P3->P1.
    D1 Result: P3 executed.

禁止：

- 历史发言原文回灌
- 主观推测 / 情绪描述
- 长句复述

第一版用纯规则按 event_type 拼字符串。LLM 压缩属于 v2 优化点。
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from contracts import EventType, FactStreamSummary

if TYPE_CHECKING:
    from contracts.schemas import PublicEvent


class SpeechSummarizer:
    """按 round 把 PublicEvent 压成 Fact Stream。

    无状态：可共享一个实例给所有 Agent。
    """

    def summarize_by_round(
        self,
        events: list[PublicEvent],
        *,
        exclude_round: int | None = None,
    ) -> list[FactStreamSummary]:
        """把多轮的 public_events 压成每轮一个 ``FactStreamSummary``。

        Args:
            events: 全部公开事件（已按 round 升序）
            exclude_round: 排除某一轮（通常 = current_round，当前轮事件不压缩，
                走 ``current_round_events`` 字段原文进 prompt）。

        Returns:
            按 round 升序的 ``FactStreamSummary`` 列表。
        """
        # 按 round 分组
        by_round: dict[int, list[PublicEvent]] = defaultdict(list)
        for ev in events:
            if exclude_round is not None and ev.round == exclude_round:
                continue
            by_round[ev.round].append(ev)

        out: list[FactStreamSummary] = []
        for round_num in sorted(by_round.keys()):
            facts = self.summarize_round(by_round[round_num])
            if facts:
                out.append(FactStreamSummary(round=round_num, facts=facts))
        return out

    def summarize_round(self, events: list[PublicEvent]) -> list[str]:
        """单轮的 PublicEvent → Fact Stream 短句列表。

        分类拼装：Daybreak / Speech / Vote / Result / Hunter / GameOver。
        """
        # 分桶
        daybreak: list[str] = []
        speeches: list[str] = []
        votes: list[str] = []
        results: list[str] = []
        hunter: list[str] = []
        game_over: list[str] = []

        for ev in events:
            et = _event_type_value(ev)
            if et == EventType.NIGHT_KILL_ANNOUNCED.value:
                if ev.target:
                    daybreak.append(f"{ev.target} died by night_kill")
            elif et == EventType.DAY_ANNOUNCEMENT.value:
                # 通常是开场播报，可能跟 NIGHT_KILL_ANNOUNCED 重合，避免重复
                if ev.summary:
                    daybreak.append(ev.summary)
            elif et == EventType.DEATH_CONFIRMED.value:
                if ev.target:
                    daybreak.append(f"{ev.target} confirmed dead")
            elif et == EventType.SPEECH.value:
                speeches.append(_summarize_speech(ev))
            elif et == EventType.VOTE_CAST.value:
                if ev.actor and ev.target:
                    votes.append(f"{ev.actor}->{ev.target}")
            elif et == EventType.TIE_DETECTED.value:
                results.append("vote tied")
            elif et == EventType.NO_EXILE_DUE_TO_SECOND_TIE.value:
                results.append("no exile (second tie)")
            elif et == EventType.EXILE.value:
                if ev.target:
                    results.append(f"{ev.target} executed")
            elif et == EventType.LAST_WORDS.value:
                if ev.actor and ev.public_message:
                    # 遗言只摘"自报角色"信息，不灌原文
                    if ev.role_claim:
                        speeches.append(
                            f"{ev.actor} last_words claim {ev.role_claim.value}"
                        )
                    else:
                        speeches.append(f"{ev.actor} gave last_words")
            elif et == EventType.HUNTER_SHOT.value:
                if ev.actor and ev.target:
                    hunter.append(f"hunter {ev.actor}->{ev.target}")
                elif ev.actor:
                    hunter.append(f"hunter {ev.actor} passed")
            elif et == EventType.GAME_OVER.value:
                if ev.summary:
                    game_over.append(f"game over: {ev.summary}")
                else:
                    game_over.append("game over")

        facts: list[str] = []
        round_prefix = f"D{events[0].round}" if events else "D?"
        if daybreak:
            facts.append(f"{round_prefix} Daybreak: " + "; ".join(daybreak) + ".")
        if speeches:
            facts.append(f"{round_prefix} Speech: " + "; ".join(speeches) + ".")
        if votes:
            facts.append(f"{round_prefix} Vote: " + "; ".join(votes) + ".")
        if results:
            facts.append(f"{round_prefix} Result: " + "; ".join(results) + ".")
        if hunter:
            facts.append(f"{round_prefix} Hunter: " + "; ".join(hunter) + ".")
        if game_over:
            facts.append(f"{round_prefix} " + "; ".join(game_over) + ".")
        return facts


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _event_type_value(ev: PublicEvent) -> str:
    et = ev.event_type
    if isinstance(et, EventType):
        return et.value
    return str(et)


def _summarize_speech(ev: PublicEvent) -> str:
    """单条 SPEECH 事件 → 短句。

    格式：
    - 自报 + 查验：``P1 self_claim Seer, check P3 Wolf``
    - 仅自报：``P1 self_claim Seer``
    - 反驳别人查验：``P3 denied`` （如果 SPEECH 没角色声明也没查验，归到 denied）
    - 普通发言：``P1 spoke``
    """
    actor = ev.actor or "?"
    if ev.role_claim and ev.claim_result:
        return (
            f"{actor} self_claim {ev.role_claim.value}, "
            f"check {ev.claim_result.target} {ev.claim_result.claimed_alignment.value}"
        )
    if ev.role_claim:
        return f"{actor} self_claim {ev.role_claim.value}"
    if ev.claim_result:
        return (
            f"{actor} report check {ev.claim_result.target} "
            f"{ev.claim_result.claimed_alignment.value}"
        )
    return f"{actor} spoke"
