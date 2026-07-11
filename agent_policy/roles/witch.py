"""女巫角色策略。

Owner：B。
"""

from __future__ import annotations

from contracts import ActionType, AgentAction, AgentContext, EventType

from agent_policy.actions import (
    build_poison_action,
    build_save_action,
    build_skip_action,
    build_speak_action,
    build_vote_action,
)
from agent_policy.belief_selectors import select_top_belief_suspect
from agent_policy.roles.strategy_base import BaseRuleBasedStrategy
from agent_policy.target_selectors import (
    alive_players,
    select_alive_non_self,
    select_public_werewolf_claim_target,
    select_tie_candidate,
)


class WitchStrategy(BaseRuleBasedStrategy):
    """女巫策略：有刀口且可救时救人，否则沿用保守默认策略。"""

    def decide_witch_night(self, context: AgentContext) -> AgentAction:
        save_target = _witch_kill_target(context)
        if save_target == context.agent_id and context.round >= 2 and ActionType.SKIP in context.allowed_actions:
            return build_skip_action(
                context,
                reason_summary="女巫第二夜及以后不能自救，看到自己是刀口时跳过避免非法自救。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "witch_self_save_forbidden_skip",
                },
            )
        if save_target and ActionType.SAVE in context.allowed_actions:
            return build_save_action(
                context,
                save_target,
                reason_summary="女巫看到夜晚刀口且解药可用，选择救人。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "witch_kill_target",
                },
            )
        poison_decision = _select_poison_target(context)
        if poison_decision and ActionType.POISON in context.allowed_actions:
            poison_target, selected_by = poison_decision
            return build_poison_action(
                context,
                poison_target,
                reason_summary="女巫没有可救刀口时，选择毒公开查杀或高置信 belief 目标。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": selected_by,
                },
            )
        if ActionType.SKIP in context.allowed_actions:
            return build_skip_action(
                context,
                reason_summary="女巫 mock 策略没有明确刀口时默认不随机用药。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "witch_default_skip",
                },
            )
        return self.decide_fallback(context)

    def decide_speech(self, context: AgentContext) -> AgentAction:
        public_claim_target = _public_claim_poison_target(context)
        if public_claim_target:
            return build_speak_action(
                context,
                f"现在公开查杀指向 {public_claim_target}，我会先看报查杀者前后视角和被查杀者回应，不急着扩大身份信息。",
                reason_summary="女巫 mock 策略隐藏身份并谨慎评估公开查杀。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "witch_public_claim_speech"},
            )
        belief_target = select_top_belief_suspect(context, min_werewolf_prob=0.65)
        if belief_target:
            return build_speak_action(
                context,
                f"{belief_target} 的嫌疑需要继续核对发言和票型，我会偏保守处理，不用单点信息强带队。",
                reason_summary="女巫 mock 策略隐藏身份并保守表达嫌疑。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "witch_belief_speech"},
            )
        return build_speak_action(
            context,
            "我会先根据公开发言和投票信息判断，不随便扩大身份信息。",
            reason_summary="女巫 mock 策略生成保守发言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "witch_cautious_speech"},
        )

    def decide_vote(self, context: AgentContext) -> AgentAction:
        public_claim_target = _public_claim_poison_target(context)
        if public_claim_target:
            return build_vote_action(
                context,
                public_claim_target,
                reason_summary="女巫 mock 策略优先投仍存活的公开查杀目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "witch_public_claim_vote"},
            )
        belief_target = select_top_belief_suspect(context, min_werewolf_prob=0.65)
        if belief_target:
            return build_vote_action(
                context,
                belief_target,
                reason_summary="女巫 mock 策略在没有合法公开查杀时，谨慎投 belief 高嫌疑目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "witch_belief_vote"},
            )
        fallback_target = select_alive_non_self(context)
        if fallback_target:
            return build_vote_action(
                context,
                fallback_target,
                reason_summary="女巫 mock 策略没有明确线索时选择合法存活目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "witch_fallback_vote"},
            )
        return self.decide_fallback(context)

    def decide_tie_revote(self, context: AgentContext) -> AgentAction:
        target = select_top_belief_suspect(
            context,
            min_werewolf_prob=0.0,
            candidate_ids=context.tie_candidates,
        )
        if target:
            return build_vote_action(
                context,
                target,
                reason_summary="女巫 mock 策略在平票候选中优先选择 belief 更高目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "witch_tie_belief_vote"},
            )
        fallback_target = select_tie_candidate(context)
        if fallback_target:
            return build_vote_action(
                context,
                fallback_target,
                reason_summary="女巫 mock 策略在平票候选人中选择合法目标。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "witch_tie_candidate"},
            )
        return self.decide_fallback(context)

    def decide_last_words(self, context: AgentContext) -> AgentAction:
        return build_speak_action(
            context,
            "我的遗言只基于公开信息，请继续回看发言和投票。",
            reason_summary="女巫 mock 策略生成遗言。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "witch_last_words"},
        )


def _witch_kill_target(context: AgentContext) -> str | None:
    legacy_target: str | None = None
    for event in context.private_events:
        if event.event_type != EventType.WITCH_KILL_TARGET_INFO or not event.target:
            continue
        if event.round == context.round:
            return event.target
        if event.round is None and legacy_target is None:
            legacy_target = event.target
    return legacy_target


def _select_poison_target(context: AgentContext) -> tuple[str, str] | None:
    public_claim_target = _public_claim_poison_target(context)
    if public_claim_target:
        return public_claim_target, "witch_public_claim_poison"
    belief_target = select_top_belief_suspect(context, min_werewolf_prob=0.75)
    if belief_target:
        return belief_target, "witch_belief_poison"
    return None


def _public_claim_poison_target(context: AgentContext) -> str | None:
    target = select_public_werewolf_claim_target(context)
    alive_ids = {player.player_id for player in alive_players(context)}
    if target and target in alive_ids:
        return target
    return None
