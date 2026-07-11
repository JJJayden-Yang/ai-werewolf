"""VisibilityRuleSpec —— 信息隔离规则的单一权威。

所有 "某 agent 能看到什么" 的规则集中在这一层，避免规则散落多处导致信息泄漏。
``ContextAssembler`` 内部调用本类四个方法生成 ``AgentContext`` 的相应字段。

可见性规则（与 Interface_v2_1 §4.1 / b_c_responsibility §2.1 对齐）：

| 信息                             | 可见者                           |
|----------------------------------|----------------------------------|
| 狼队友身份 / 狼夜提名            | 所有狼人                         |
| 预言家查验结果                   | 预言家本人                       |
| 女巫刀口 / 用药记录              | 女巫本人                         |
| ``public_events`` 公开事件       | 所有 Agent                       |
| ``TruthState.role_map``          | **任何 Agent 都不可见**          |
| shadow Belief                    | **任何 Agent 都不可见**          |
| 系统事件（PHASE_STARTED 等）     | **不进 AI 简报**（不管 visibility）|

红线：

- 只读 ``GameSession``，**绝不**把 ``GameSession`` / ``TruthState`` 对象传出去
- ``visible_players`` 暴露 ``player_id / status / public_claim``，**不暴露 ``role``**
- ``GameEvent`` → ``PublicEvent`` / ``PrivateEvent`` 转换时丢弃 ``payload`` 里
  的系统字段（如 ``fallback_used``），只保留 AI 可见字段

双层过滤策略（fail-safe）：

1. **第一层：event_type 白名单** —— 只有 AI 可见的事件类型才考虑
2. **第二层：visibility 枚举** —— PUBLIC 进 public_events；私密事件按角色匹配

即使 A 给 ``role_assigned`` 误标 ``visibility=public``，第一层白名单就能拦住
（``role_assigned`` 不在 AI 可见事件白名单里），不会泄漏游戏真相。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts import (
    ActionType,
    ClaimResult,
    EventType,
    Phase,
    PrivateEvent,
    PublicEvent,
    Role,
    VisiblePlayer,
    Visibility,
)

if TYPE_CHECKING:
    from contracts.schemas import GameEvent
    from game_core.types import GameSession


# ---------------------------------------------------------------------------
# 事件分类白名单
# ---------------------------------------------------------------------------

# **AI 可见的公开事件**（进入 AgentContext.public_events）。
#
# 注意：NIGHT_KILL_ANNOUNCED 是夜间结算内部刀口，不是白天公开信息。
# 白天玩家只应通过 DAY_ANNOUNCEMENT / DEATH_CONFIRMED 看到死亡结果；
# 平安夜时普通玩家不能反推出谁被刀、谁被救。
PUBLIC_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.DAY_ANNOUNCEMENT,
        EventType.SPEECH,
        EventType.VOTE_CAST,
        EventType.TIE_DETECTED,
        EventType.NO_EXILE_DUE_TO_SECOND_TIE,
        EventType.EXILE,
        EventType.LAST_WORDS,
        EventType.HUNTER_SHOT,
        EventType.DEATH_CONFIRMED,
        EventType.GAME_OVER,
    }
)

# **AI 可见的私密事件**（进入 AgentContext.private_events，按角色匹配）
PRIVATE_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.WOLF_NOMINATION,
        EventType.SEER_CHECK_RESULT,
        EventType.WITCH_KILL_TARGET_INFO,
        EventType.WITCH_SAVE,
        EventType.WITCH_POISON,
    }
)

# 私密事件 visibility → 允许看的角色集合。
_VISIBILITY_TO_ROLES: dict[Visibility, frozenset[Role]] = {
    Visibility.PRIVATE_TO_WOLVES: frozenset({Role.WEREWOLF}),
    Visibility.PRIVATE_TO_SEER: frozenset({Role.SEER}),
    Visibility.PRIVATE_TO_WITCH: frozenset({Role.WITCH}),
}


class VisibilityRuleSpec:
    """信息隔离规则的单一权威。

    无状态：可共享一个实例给所有 Agent。
    """

    # --- public events ------------------------------------------------------

    def visible_public_events(
        self, events: list[GameEvent], session: GameSession, agent_id: str
    ) -> list[PublicEvent]:
        """从所有 ``GameEvent`` 中筛出对所有人公开的，转 ``PublicEvent``。

        过滤规则：``event_type ∈ PUBLIC_EVENT_TYPES`` **且** ``visibility == PUBLIC``。
        """
        out: list[PublicEvent] = []
        for ev in events:
            event_type_enum = _safe_event_type(ev)
            if event_type_enum is None or event_type_enum not in PUBLIC_EVENT_TYPES:
                continue
            if _safe_visibility(ev) != Visibility.PUBLIC:
                continue
            out.append(_to_public_event(ev))
        return out

    # --- private events -----------------------------------------------------

    def visible_private_events(
        self, events: list[GameEvent], session: GameSession, agent_id: str
    ) -> list[PrivateEvent]:
        """从所有 ``GameEvent`` 中筛出对该 agent 私密可见的，转 ``PrivateEvent``。

        过滤规则：
        - ``event_type ∈ PRIVATE_EVENT_TYPES``（白名单）
        - ``visibility`` 跟 agent 的 ``role`` 匹配（按 _VISIBILITY_TO_ROLES）
        - actor 约束：WOLF_NOMINATION 对全狼公开；其他私密事件 actor 应为本人或 None
        """
        if agent_id not in session.truth_state.players:
            return []
        agent_role = session.truth_state.players[agent_id].role

        out: list[PrivateEvent] = []
        for ev in events:
            event_type_enum = _safe_event_type(ev)
            if event_type_enum is None or event_type_enum not in PRIVATE_EVENT_TYPES:
                continue
            visibility_enum = _safe_visibility(ev)
            allowed_roles = _VISIBILITY_TO_ROLES.get(visibility_enum)
            if allowed_roles is None or agent_role not in allowed_roles:
                continue
            if not _agent_can_see_private_actor(ev, agent_id, agent_role):
                continue
            out.append(_to_private_event(ev))
        return out

    # --- visible players ----------------------------------------------------

    def observer_role(self, session: GameSession, agent_id: str) -> Role | None:
        """返回 agent 自己的身份，供非 AgentContext 投递层做可见性/主观视角计算。

        这不是 ``visible_players`` 的一部分，不会暴露给其他玩家；等价于 AgentContext.role
        中"玩家知道自己身份"的那一项信息。
        """
        player = session.truth_state.players.get(agent_id)
        return player.role if player is not None else None

    def visible_players(
        self, session: GameSession, agent_id: str
    ) -> list[VisiblePlayer]:
        """暴露 ``player_id / status / public_claim`` —— **不暴露 ``role``**。

        即使 agent 是狼人，他的 ``visible_players`` 里也看不到队友的 role；
        队友信息通过 ``private_events`` 里的 ``WOLF_NOMINATION.teammates`` 传递。
        输出按 ``player_id`` 字典序排序，保证确定性。
        """
        players = session.truth_state.players
        return [
            VisiblePlayer(
                player_id=pid,
                status=p.status,
                public_claim=p.public_claim,
            )
            for pid, p in sorted(players.items())
        ]

    # --- allowed actions ----------------------------------------------------

    def allowed_actions(
        self, session: GameSession, agent_id: str, phase: Phase
    ) -> list[ActionType]:
        """当前 phase 该 agent 能做的动作集合。

        复用 A 的 ``RuleValidator.allowed_actions(phase)`` 单一来源拿 phase 级
        允许集，再按 agent 状态叠加收窄。RuleValidator.allowed_actions docstring
        明确写"按 agent 收窄由调用方叠加"，C 是装配 AgentContext 的调用方。

        NIGHT_WITCH：解药/毒药用过后从集合里移除对应动作，避免女巫策略走出
        会被 RuleValidator 拦截的非法动作而触发 fallback。读 ``truth_state``
        仅用于此处计算，不进 AgentContext，不泄漏给 agent。
        """
        from game_core.rule_validator import RuleValidator

        allowed = set(RuleValidator.allowed_actions(phase))

        if phase == Phase.NIGHT_WITCH:
            player = session.truth_state.players.get(agent_id)
            if player and player.role == Role.WITCH:
                witch_state = session.truth_state.witch_state
                if witch_state.antidote_used:
                    allowed.discard(ActionType.SAVE)
                if witch_state.poison_used:
                    allowed.discard(ActionType.POISON)

        return sorted(allowed, key=lambda a: a.value)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _safe_event_type(ev: GameEvent) -> EventType | None:
    """从 GameEvent.event_type（可能是 str 或 enum）安全转 EventType。"""
    if isinstance(ev.event_type, EventType):
        return ev.event_type
    try:
        return EventType(ev.event_type)
    except (ValueError, TypeError):
        return None


def _safe_visibility(ev: GameEvent) -> Visibility | None:
    """从 GameEvent.visibility（可能是 str 或 enum）安全转 Visibility。"""
    if isinstance(ev.visibility, Visibility):
        return ev.visibility
    try:
        return Visibility(ev.visibility)
    except (ValueError, TypeError):
        return None


def _to_public_event(ev: GameEvent) -> PublicEvent:
    """从 GameEvent 拷出 PublicEvent；payload 里的 AI 可见字段尽量提取。"""
    payload = ev.payload or {}
    event_type_enum = _safe_event_type(ev) or EventType.DAY_ANNOUNCEMENT
    return PublicEvent(
        event_id=ev.event_id,
        round=ev.round,
        phase=ev.phase,
        event_type=event_type_enum,
        actor=ev.actor,
        target=ev.target,
        public_message=payload.get("public_message"),
        role_claim=_safe_role(payload.get("role_claim")),
        claim_result=_safe_claim_result(payload.get("claim_result")),
        summary=payload.get("summary"),
    )


def _to_private_event(ev: GameEvent) -> PrivateEvent:
    """从 GameEvent 拷出 PrivateEvent。

    携带 ``round`` 让消费方按需取舍：女巫取 max round 拿当晚刀口；
    预言家忽略 round 读全量查验史；狼队友 roster 跨轮共用。
    """
    payload = ev.payload or {}
    event_type_enum = _safe_event_type(ev)
    return PrivateEvent(
        event_type=event_type_enum or EventType.WOLF_NOMINATION,
        round=ev.round,
        target=ev.target,
        result=payload.get("result"),
        teammates=payload.get("teammates"),
        visibility=_safe_visibility(ev),
    )


def _safe_role(v) -> Role | None:
    if v is None:
        return None
    if isinstance(v, Role):
        return v
    try:
        return Role(v)
    except (ValueError, TypeError):
        return None


def _safe_claim_result(v) -> ClaimResult | None:
    """payload 里 claim_result 可能是 dict、已是 ClaimResult、或 None。"""
    if v is None:
        return None
    if isinstance(v, ClaimResult):
        return v
    if isinstance(v, dict):
        try:
            return ClaimResult.model_validate(v)
        except Exception:
            return None
    return None


def _agent_can_see_private_actor(
    ev: GameEvent, agent_id: str, agent_role: Role
) -> bool:
    """私密事件的 actor 限制规则。

    - WOLF_NOMINATION：对全狼公开（actor 可以是任意狼，包括队友）
    - SEER_CHECK_RESULT / WITCH_*：actor 应为本人（自己做的事自己看），或 None（系统填的）
    """
    event_type_enum = _safe_event_type(ev)
    if event_type_enum == EventType.WOLF_NOMINATION:
        return agent_role == Role.WEREWOLF
    if event_type_enum in {
        EventType.SEER_CHECK_RESULT,
        EventType.WITCH_KILL_TARGET_INFO,
        EventType.WITCH_SAVE,
        EventType.WITCH_POISON,
    }:
        return ev.actor is None or ev.actor == agent_id
    return True
