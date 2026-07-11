"""Agent 目标选择纯函数。

所有函数只读取 AgentContext，不读取 TruthState / GameSession / Store。
"""

from __future__ import annotations

from contracts import AgentContext, ClaimedAlignment, EventType, PlayerStatus, Role, VisiblePlayer


def alive_players(context: AgentContext) -> list[VisiblePlayer]:
    """返回 Agent 当前可见的存活玩家。"""
    return [p for p in context.visible_players if p.status == PlayerStatus.ALIVE]


def select_alive_non_self(context: AgentContext) -> str | None:
    """选择一个存活且不是自己的玩家。"""
    for player in alive_players(context):
        if player.player_id != context.agent_id:
            return player.player_id
    return None


def wolf_teammates_from_private_events(context: AgentContext) -> set[str]:
    """从 private_events 提取狼人队友信息。"""
    teammates: set[str] = set()
    for event in context.private_events:
        if event.teammates:
            teammates.update(event.teammates)
    return teammates


def select_wolf_kill_target(context: AgentContext) -> str | None:
    """狼人选择存活且非狼队友目标。"""
    teammates = wolf_teammates_from_private_events(context)
    for player in alive_players(context):
        if player.player_id != context.agent_id and player.player_id not in teammates:
            return player.player_id
    return None


def checked_targets_from_private_events(context: AgentContext) -> set[str]:
    """从预言家的私密事件中读取已查验目标。"""
    checked: set[str] = set()
    for event in context.private_events:
        if event.event_type == EventType.SEER_CHECK_RESULT and event.target:
            checked.add(event.target)
    return checked


def select_unchecked_player(context: AgentContext) -> str | None:
    """预言家优先选择未查验、存活、非自己的玩家。"""
    checked = checked_targets_from_private_events(context)
    for player in alive_players(context):
        if player.player_id != context.agent_id and player.player_id not in checked:
            return player.player_id
    return None


def select_tie_candidate(context: AgentContext) -> str | None:
    """二次投票只在 tie_candidates 里选择存活非自己玩家。"""
    alive_ids = {p.player_id for p in alive_players(context)}
    for candidate in context.tie_candidates:
        if candidate != context.agent_id and candidate in alive_ids:
            return candidate
    return None


def select_claimed_seer(context: AgentContext) -> str | None:
    """选择公开跳预言家的存活玩家。"""
    for player in alive_players(context):
        if player.player_id != context.agent_id and player.public_claim == Role.SEER.value:
            return player.player_id
    return None


def select_public_werewolf_claim_target(context: AgentContext) -> str | None:
    """从公开查杀发言里读取被声称为狼的目标。"""
    for event in context.public_events + context.current_round_events + context.recent_public_events:
        if (
            event.claim_result
            and event.claim_result.target
            and event.claim_result.claimed_alignment == ClaimedAlignment.WEREWOLF
            and event.claim_result.target != context.agent_id
        ):
            return event.claim_result.target
    return None
