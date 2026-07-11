"""MockAgent 专用策略函数。

这些函数只服务 LegalRandomMockAgent / HeuristicMockAgent。
真实 LLM Agent 后续走 RoleStrategy / prompt / runtime，不依赖本模块。
"""

from __future__ import annotations

from contracts import ActionType, AgentAction, AgentContext, EventType, Phase

from agent_policy.actions import (
    build_check_action,
    build_hunter_shoot_action,
    build_save_action,
    build_skip_action,
    build_speak_action,
    build_vote_action,
    build_wolf_nomination_action,
)
from agent_policy.target_selectors import (
    select_alive_non_self,
    select_claimed_seer,
    select_public_werewolf_claim_target,
    select_tie_candidate,
    select_unchecked_player,
    select_wolf_kill_target,
)


def legal_random_policy(context: AgentContext) -> AgentAction:
    """第一阶段合法 Mock 策略。

    当前实现为确定性选择第一个合法目标，便于测试复现。
    """
    if context.phase == Phase.NIGHT_WEREWOLF:
        target = select_wolf_kill_target(context)
        if target:
            return build_wolf_nomination_action(
                context,
                target,
                metadata={"policy": "legal_random", "selected_by": "first_legal_non_teammate"},
            )
        return build_skip_action(context, metadata={"policy": "legal_random"})

    if context.phase == Phase.NIGHT_SEER:
        target = select_unchecked_player(context)
        if target:
            return build_check_action(
                context,
                target,
                metadata={"policy": "legal_random", "selected_by": "first_unchecked"},
            )
        return build_skip_action(context, metadata={"policy": "legal_random"})

    if context.phase == Phase.NIGHT_WITCH:
        return build_skip_action(
            context,
            reason_summary="女巫默认不随机使用药。",
            metadata={"policy": "legal_random", "selected_by": "witch_default_skip"},
        )

    if context.phase in {Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION}:
        return build_speak_action(
            context,
            "我会继续观察大家的发言和投票。",
            reason_summary="生成中性发言。",
            metadata={"policy": "legal_random", "selected_by": "neutral_speech"},
        )

    if context.phase == Phase.DAY_VOTE:
        target = select_alive_non_self(context)
        if target:
            return build_vote_action(
                context,
                target,
                metadata={"policy": "legal_random", "selected_by": "first_alive_non_self"},
            )
        return build_skip_action(context, metadata={"policy": "legal_random"})

    if context.phase == Phase.DAY_TIE_REVOTE:
        target = select_tie_candidate(context)
        if target:
            return build_vote_action(
                context,
                target,
                metadata={"policy": "legal_random", "selected_by": "first_tie_candidate"},
            )
        return build_skip_action(context, metadata={"policy": "legal_random"})

    if context.phase == Phase.HUNTER_SHOOT:
        target = select_alive_non_self(context)
        return build_hunter_shoot_action(
            context,
            target,
            metadata={"policy": "legal_random", "selected_by": "first_alive_or_pass"},
        )

    if context.phase == Phase.EXILE_LAST_WORDS:
        return build_speak_action(
            context,
            "这是我的遗言，请大家继续根据发言和投票判断。",
            reason_summary="生成默认遗言。",
            metadata={"policy": "legal_random", "selected_by": "default_last_words"},
        )

    if ActionType.SKIP in context.allowed_actions:
        return build_skip_action(context, metadata={"policy": "legal_random"})

    return build_speak_action(
        context,
        "我暂时没有更多信息。",
        metadata={"policy": "legal_random", "selected_by": "safe_default_speech"},
    )


def heuristic_policy(context: AgentContext) -> AgentAction:
    """第一阶段启发式 Mock 策略。"""
    if context.phase == Phase.NIGHT_WEREWOLF:
        claimed_seer = select_claimed_seer(context)
        if claimed_seer:
            return build_wolf_nomination_action(
                context,
                claimed_seer,
                reason_summary="优先击杀公开跳预言家的玩家。",
                metadata={"policy": "heuristic", "selected_by": "claimed_seer"},
            )
        return legal_random_policy(context)

    if context.phase == Phase.NIGHT_WITCH:
        kill_target = _witch_kill_target(context)
        if kill_target and ActionType.SAVE in context.allowed_actions:
            return build_save_action(
                context,
                kill_target,
                reason_summary="女巫第一阶段启发式：有刀口时默认救。",
                metadata={"policy": "heuristic", "selected_by": "witch_kill_target"},
            )
        return legal_random_policy(context)

    if context.phase in {Phase.DAY_VOTE, Phase.DAY_TIE_REVOTE}:
        claimed_wolf = select_public_werewolf_claim_target(context)
        if claimed_wolf:
            return build_vote_action(
                context,
                claimed_wolf,
                reason_summary="优先投公开查杀目标。",
                metadata={"policy": "heuristic", "selected_by": "public_werewolf_claim"},
            )
        return legal_random_policy(context)

    return legal_random_policy(context)


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
