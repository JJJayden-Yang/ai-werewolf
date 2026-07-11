"""平民角色策略。

Owner：B。
"""

from __future__ import annotations

from contracts import AgentAction, AgentContext, Phase

from agent_policy.actions import build_speak_action, build_vote_action
from agent_policy.belief_selectors import select_top_belief_suspect
from agent_policy.roles.strategy_base import BaseRuleBasedStrategy
from agent_policy.target_selectors import (
    alive_players,
    select_alive_non_self,
    select_public_werewolf_claim_target,
    select_tie_candidate,
)


class VillagerStrategy(BaseRuleBasedStrategy):
    """平民策略：白天发言和投票。"""

    def decide_speech(self, context: AgentContext) -> AgentAction:
        if context.phase == Phase.DAY_TIE_DISCUSSION and context.tie_candidates:
            candidates = "、".join(context.tie_candidates)
            return build_speak_action(
                context,
                f"现在平票候选是 {candidates}，我会重点回看他们的发言和上一轮投票理由。",
                reason_summary="平民 mock 策略围绕平票候选人发言。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "villager_tie_discussion_speech"},
            )

        public_claim_target = _alive_public_werewolf_claim_target(context)
        if public_claim_target:
            return build_speak_action(
                context,
                f"目前公开查杀指向 {public_claim_target}，我会核对报查杀者视角和被查杀者回应，不无脑跟票。",
                reason_summary="平民 mock 策略围绕公开查杀发言。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "villager_public_claim_speech"},
            )

        belief_target = select_top_belief_suspect(context, min_werewolf_prob=0.55)
        if belief_target:
            return build_speak_action(
                context,
                f"从现有公开信息看，{belief_target} 的嫌疑偏高，但我不会把 belief 当成真实身份。",
                reason_summary="平民 mock 策略基于 belief 生成嫌疑发言。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "villager_belief_speech"},
            )

        return build_speak_action(
            context,
            "我会结合当前信息继续观察大家的发言和投票。",
            reason_summary="平民 mock 策略生成中性发言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "villager_neutral_speech"},
        )

    def decide_vote(self, context: AgentContext) -> AgentAction:
        public_claim_target = _alive_public_werewolf_claim_target(context)
        if public_claim_target:
            return build_vote_action(
                context,
                public_claim_target,
                reason_summary="平民优先投仍存活的公开查杀目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "villager_public_claim_vote"},
            )
        belief_target = select_top_belief_suspect(context, min_werewolf_prob=0.55)
        if belief_target:
            return build_vote_action(
                context,
                belief_target,
                reason_summary="平民在没有合法公开查杀时，投 belief 高嫌疑目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "villager_belief_vote"},
            )
        fallback_target = select_alive_non_self(context)
        if fallback_target:
            return build_vote_action(
                context,
                fallback_target,
                reason_summary="平民没有明确线索时选择一个合法存活目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "villager_fallback_vote"},
            )
        return self.decide_fallback(context)

    def decide_tie_revote(self, context: AgentContext) -> AgentAction:
        target = select_top_belief_suspect(
            context,
            min_werewolf_prob=0.0,
            candidate_ids=context.tie_candidates,
        ) or select_tie_candidate(context)
        if target:
            return build_vote_action(
                context,
                target,
                reason_summary="平民二次投票优先在平票候选人中选择 belief 更高的目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "villager_tie_candidate"},
            )
        return self.decide_fallback(context)

    def decide_last_words(self, context: AgentContext) -> AgentAction:
        return build_speak_action(
            context,
            "这是我的遗言，请大家回看发言和投票。",
            reason_summary="平民 mock 策略生成默认遗言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "villager_last_words"},
        )


def _alive_public_werewolf_claim_target(context: AgentContext) -> str | None:
    target = select_public_werewolf_claim_target(context)
    alive_ids = {player.player_id for player in alive_players(context)}
    if target and target in alive_ids:
        return target
    return None
