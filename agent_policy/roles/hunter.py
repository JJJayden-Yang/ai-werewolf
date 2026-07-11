"""猎人角色策略。

Owner：B，Phase 3 9 人 mock 补充。
"""

from __future__ import annotations

from contracts import AgentAction, AgentContext

from agent_policy.actions import build_hunter_shoot_action, build_speak_action, build_vote_action
from agent_policy.belief_selectors import select_top_belief_suspect
from agent_policy.roles.strategy_base import BaseRuleBasedStrategy
from agent_policy.target_selectors import (
    alive_players,
    select_alive_non_self,
    select_public_werewolf_claim_target,
    select_tie_candidate,
)


class HunterStrategy(BaseRuleBasedStrategy):
    """猎人策略：白天正常发言投票，开枪阶段高置信才开枪。"""

    def decide_speech(self, context: AgentContext) -> AgentAction:
        return build_speak_action(
            context,
            "我会按普通好人视角整理公开发言、投票关系和身份声明，先把可复盘的理由说清楚。",
            reason_summary="猎人 mock 策略隐藏身份并生成白天谨慎发言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "hunter_hidden_day_speech"},
        )

    def decide_vote(self, context: AgentContext) -> AgentAction:
        target = _alive_public_werewolf_claim_target(context)
        selected_by = "hunter_public_claim_vote"
        if not target:
            target = select_top_belief_suspect(context, min_werewolf_prob=0.55)
            selected_by = "hunter_belief_vote"
        if not target:
            target = select_alive_non_self(context)
            selected_by = "hunter_fallback_vote"
        return build_vote_action(
            context,
            target or context.agent_id,
            reason_summary="猎人 mock 策略按公开查杀、belief 嫌疑、合法目标顺序投票。",
            metadata={"strategy": self.__class__.__name__, "selected_by": selected_by},
        )

    def decide_tie_revote(self, context: AgentContext) -> AgentAction:
        target = (
            select_top_belief_suspect(
                context,
                min_werewolf_prob=0.0,
                candidate_ids=context.tie_candidates,
            )
            or select_tie_candidate(context)
            or select_alive_non_self(context)
        )
        return build_vote_action(
            context,
            target or context.agent_id,
            reason_summary="猎人 mock 策略在平票候选中优先选择 belief 更高目标。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "hunter_tie_revote"},
        )

    def decide_last_words(self, context: AgentContext) -> AgentAction:
        return build_speak_action(
            context,
            "我是猎人遗言：请继续回看发言、投票关系和我之前给出的怀疑顺序。",
            reason_summary="猎人 mock 策略生成遗言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "hunter_last_words"},
        )

    def decide_hunter_shoot(self, context: AgentContext) -> AgentAction:
        target = select_top_belief_suspect(context, min_werewolf_prob=0.70)
        if target:
            return build_hunter_shoot_action(
                context,
                target,
                reason_summary="猎人 mock 策略选择射击 belief 最高的高嫌疑存活目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "hunter_belief_suspect"},
            )
        return build_hunter_shoot_action(
            context,
            None,
            reason_summary="猎人 mock 策略没有高置信嫌疑目标时默认不开枪。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "hunter_pass"},
        )


def _alive_public_werewolf_claim_target(context: AgentContext) -> str | None:
    target = select_public_werewolf_claim_target(context)
    alive_ids = {player.player_id for player in alive_players(context)}
    if target and target in alive_ids:
        return target
    return None
