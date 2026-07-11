"""狼人角色策略。

Owner：A。B 负责接口维护和合并协调。
"""

from __future__ import annotations

from contracts import AgentAction, AgentContext

from agent_policy.actions import build_speak_action, build_vote_action, build_wolf_nomination_action
from agent_policy.roles.strategy_base import BaseRuleBasedStrategy
from agent_policy.target_selectors import (
    alive_players,
    select_claimed_seer,
    select_public_werewolf_claim_target,
    select_tie_candidate,
    select_wolf_kill_target,
    wolf_teammates_from_private_events,
)


class WerewolfStrategy(BaseRuleBasedStrategy):
    """狼人策略：夜晚优先处理公开跳预言家的目标，其次避开狼队友选择目标。"""

    def decide_werewolf_night(self, context: AgentContext) -> AgentAction:
        # 公开跳预言家者优先——但若那是悍跳的狼队友则跳过（否则提名队友会被
        # RuleValidator 以 wolf_cannot_kill_teammate 拦掉，退化成 fallback）。
        teammates = wolf_teammates_from_private_events(context)
        claimed_seer = select_claimed_seer(context)
        if claimed_seer is not None and claimed_seer in teammates:
            claimed_seer = None
        target = claimed_seer or select_wolf_kill_target(context)
        if target:
            return build_wolf_nomination_action(
                context,
                target,
                reason_summary="狼人优先击杀公开跳预言家或非队友目标。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "claimed_seer_or_non_teammate",
                },
            )
        return self.decide_fallback(context)

    def decide_speech(self, context: AgentContext) -> AgentAction:
        return build_speak_action(
            context,
            "我会先按公开发言和投票关系来判断，不急着站边。",
            reason_summary="狼人 mock 策略白天伪装普通好人发言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "werewolf_bluff_speech"},
        )

    def decide_vote(self, context: AgentContext) -> AgentAction:
        # 跟公开查杀走，但绝不投自己的狼队友（哪怕队友被真预言家查杀，跟投等于
        # 亲手送走队友）；被查杀者是队友时改投其他存活非队友。
        teammates = wolf_teammates_from_private_events(context)
        claimed = select_public_werewolf_claim_target(context)
        if claimed is not None and claimed not in teammates:
            target = claimed
        else:
            target = _first_alive_non_teammate(context, teammates)
        if target:
            return build_vote_action(
                context,
                target,
                reason_summary="狼人 mock 策略按公开信息投票，避免投出狼队友。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "werewolf_public_vote"},
            )
        return self.decide_fallback(context)

    def decide_tie_revote(self, context: AgentContext) -> AgentAction:
        # 二次投票只能投 tie_candidates，优先投非队友候选人。
        teammates = wolf_teammates_from_private_events(context)
        target = _first_tie_candidate_non_teammate(context, teammates) or select_tie_candidate(context)
        if target:
            return build_vote_action(
                context,
                target,
                reason_summary="狼人 mock 策略在平票候选人中优先选择非队友目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "werewolf_tie_candidate"},
            )
        return self.decide_fallback(context)

    def decide_last_words(self, context: AgentContext) -> AgentAction:
        return build_speak_action(
            context,
            "我出局后你们重点回看投票链，不要只听单点发言。",
            reason_summary="狼人 mock 策略生成伪装遗言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "werewolf_last_words"},
        )


# 狼人专属的"避队友"目标过滤（不通用，故放本地而非 B 协调的 target_selectors）。
def _first_alive_non_teammate(context: AgentContext, teammates: set[str]) -> str | None:
    """第一个存活、非自己、非狼队友的玩家。"""
    for player in alive_players(context):
        pid = player.player_id
        if pid != context.agent_id and pid not in teammates:
            return pid
    return None


def _first_tie_candidate_non_teammate(context: AgentContext, teammates: set[str]) -> str | None:
    """tie_candidates 中第一个存活、非自己、非狼队友的候选人。"""
    alive_ids = {p.player_id for p in alive_players(context)}
    for pid in context.tie_candidates:
        if pid != context.agent_id and pid not in teammates and pid in alive_ids:
            return pid
    return None
