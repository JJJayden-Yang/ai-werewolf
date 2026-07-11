"""AgentAction 构造器与角色动作注册表。

这里的 action builder 只生成标准 AgentAction，不改变游戏世界。
真正结算由 A 的 RuleValidator / ActionResolver 完成。
"""

from __future__ import annotations

from collections.abc import Callable

from contracts import ActionType, AgentAction, AgentContext, Role


ROLE_ACTIONS: dict[Role, set[ActionType]] = {
    Role.WEREWOLF: {
        ActionType.SPEAK,
        ActionType.VOTE,
        ActionType.NIGHT_KILL_NOMINATE,
    },
    Role.SEER: {
        ActionType.SPEAK,
        ActionType.VOTE,
        ActionType.CHECK,
    },
    Role.WITCH: {
        ActionType.SPEAK,
        ActionType.VOTE,
        ActionType.SAVE,
        ActionType.POISON,
        ActionType.SKIP,
    },
    Role.HUNTER: {
        ActionType.SPEAK,
        ActionType.VOTE,
        ActionType.HUNTER_SHOOT,
    },
    Role.VILLAGER: {
        ActionType.SPEAK,
        ActionType.VOTE,
    },
}


def available_actions_for_context(context: AgentContext) -> set[ActionType]:
    """返回角色动作与当前阶段允许动作的交集。"""
    return ROLE_ACTIONS[context.role] & set(context.allowed_actions)


def _base_action(
    context: AgentContext,
    action_type: ActionType,
    *,
    target: str | None = None,
    public_message: str | None = None,
    reason_summary: str | None = None,
    metadata: dict | None = None,
) -> AgentAction:
    return AgentAction(
        game_id=context.game_id,
        agent_id=context.agent_id,
        role=context.role,
        phase=context.phase,
        action_type=action_type,
        target=target,
        public_message=public_message,
        reason_summary=reason_summary,
        metadata={
            "policy_module": "agent_policy.actions",
            **(metadata or {}),
        },
    )


def build_vote_action(
    context: AgentContext,
    target: str,
    *,
    reason_summary: str = "选择一个合法投票目标。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.VOTE,
        target=target,
        reason_summary=reason_summary,
        metadata=metadata,
    )


def build_speak_action(
    context: AgentContext,
    public_message: str,
    *,
    reason_summary: str = "生成当前阶段发言。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.SPEAK,
        public_message=public_message,
        reason_summary=reason_summary,
        metadata=metadata,
    )


def build_wolf_nomination_action(
    context: AgentContext,
    target: str,
    *,
    reason_summary: str = "选择一个存活非狼队友作为击杀提名。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.NIGHT_KILL_NOMINATE,
        target=target,
        reason_summary=reason_summary,
        metadata=metadata,
    )


def build_check_action(
    context: AgentContext,
    target: str,
    *,
    reason_summary: str = "选择一个未查验目标。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.CHECK,
        target=target,
        reason_summary=reason_summary,
        metadata=metadata,
    )


def build_save_action(
    context: AgentContext,
    target: str,
    *,
    reason_summary: str = "女巫使用解药救人。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.SAVE,
        target=target,
        reason_summary=reason_summary,
        metadata=metadata,
    )


def build_poison_action(
    context: AgentContext,
    target: str,
    *,
    reason_summary: str = "女巫使用毒药。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.POISON,
        target=target,
        reason_summary=reason_summary,
        metadata=metadata,
    )


def build_hunter_shoot_action(
    context: AgentContext,
    target: str | None = None,
    *,
    reason_summary: str = "猎人开枪或跳过。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.HUNTER_SHOOT,
        target=target,
        reason_summary=reason_summary,
        metadata=metadata,
    )


def build_skip_action(
    context: AgentContext,
    *,
    reason_summary: str = "当前阶段选择跳过。",
    metadata: dict | None = None,
) -> AgentAction:
    return _base_action(
        context,
        ActionType.SKIP,
        reason_summary=reason_summary,
        metadata=metadata,
    )


ACTION_BUILDERS: dict[ActionType, Callable[..., AgentAction]] = {
    ActionType.SPEAK: build_speak_action,
    ActionType.VOTE: build_vote_action,
    ActionType.NIGHT_KILL_NOMINATE: build_wolf_nomination_action,
    ActionType.CHECK: build_check_action,
    ActionType.SAVE: build_save_action,
    ActionType.POISON: build_poison_action,
    ActionType.HUNTER_SHOOT: build_hunter_shoot_action,
    ActionType.SKIP: build_skip_action,
}

