"""W2（Yuan）：狼人策略「避队友」补强。

依赖 W1 已把狼队友名单喂进 `AgentContext.private_events[*].teammates`。本测试验证狼
在白天投票、夜晚刀人、平票二次投票时都不会误伤队友：

- 投票：不跟投被公开查杀的狼队友；
- 夜刀：不提名悍跳预言家的狼队友（否则 RuleValidator 拦截 → fallback）；
- 平票：优先投非队友候选人。
"""

from contracts import (
    ActionType,
    AgentContext,
    ClaimedAlignment,
    ClaimResult,
    EventType,
    Phase,
    PlayerStatus,
    PrivateEvent,
    PublicEvent,
    Role,
    Visibility,
    VisiblePlayer,
)

from agent_policy.roles.werewolf import WerewolfStrategy


def _wolf_context(
    *,
    phase: Phase,
    allowed_actions: list[ActionType],
    visible: list[VisiblePlayer],
    public_events=None,
    tie_candidates=None,
    teammates=("P1", "P2"),
) -> AgentContext:
    """agent=P1 狼，队友 P1/P2（经 WOLF_NOMINATION 私有事件传递，符合 W1 真实载体）。"""
    return AgentContext(
        game_id="g_w2",
        agent_id="P1",
        role=Role.WEREWOLF,
        round=1,
        phase=phase,
        visible_players=visible,
        private_events=[
            PrivateEvent(
                event_type=EventType.WOLF_NOMINATION,
                teammates=list(teammates),
                visibility=Visibility.PRIVATE_TO_WOLVES,
            )
        ],
        public_events=public_events or [],
        tie_candidates=list(tie_candidates or []),
        allowed_actions=allowed_actions,
    )


def _alive(*pids: str, claims: dict[str, str] | None = None) -> list[VisiblePlayer]:
    claims = claims or {}
    return [
        VisiblePlayer(player_id=pid, status=PlayerStatus.ALIVE, public_claim=claims.get(pid))
        for pid in pids
    ]


def _public_werewolf_claim(target: str) -> list:
    return [
        PublicEvent(
            event_id="evt_claim",
            round=1,
            phase=Phase.DAY_DISCUSSION,
            event_type=EventType.SPEECH,
            actor="P3",
            public_message=f"我查验 {target} 是狼人。",
            role_claim=Role.SEER,
            claim_result=ClaimResult(target=target, claimed_alignment=ClaimedAlignment.WEREWOLF),
        )
    ]


def test_vote_does_not_follow_public_kill_on_teammate():
    """队友 P2 被公开查杀时，狼不跟投 P2，改投存活非队友。"""
    ctx = _wolf_context(
        phase=Phase.DAY_VOTE,
        allowed_actions=[ActionType.VOTE],
        visible=_alive("P1", "P2", "P3", "P4"),
        public_events=_public_werewolf_claim("P2"),
    )
    action = WerewolfStrategy().decide(ctx)
    assert action.action_type == ActionType.VOTE
    assert action.target == "P3"  # 第一个存活非队友
    assert action.target not in {"P1", "P2"}


def test_vote_follows_public_kill_when_target_is_not_teammate():
    """被查杀的是非队友 P3 时，狼乐意跟投（送走好人）。"""
    ctx = _wolf_context(
        phase=Phase.DAY_VOTE,
        allowed_actions=[ActionType.VOTE],
        visible=_alive("P1", "P2", "P3", "P4"),
        public_events=_public_werewolf_claim("P3"),
    )
    action = WerewolfStrategy().decide(ctx)
    assert action.action_type == ActionType.VOTE
    assert action.target == "P3"


def test_night_does_not_nominate_teammate_who_fake_claims_seer():
    """队友 P2 悍跳预言家时，狼夜刀不提名 P2（避免被规则拦成 fallback）。"""
    ctx = _wolf_context(
        phase=Phase.NIGHT_WEREWOLF,
        allowed_actions=[ActionType.NIGHT_KILL_NOMINATE],
        visible=_alive("P1", "P2", "P3", "P4", claims={"P2": Role.SEER.value}),
    )
    action = WerewolfStrategy().decide(ctx)
    assert action.action_type == ActionType.NIGHT_KILL_NOMINATE
    assert action.target not in {"P1", "P2"}
    assert action.target == "P3"


def test_night_targets_non_teammate_claimed_seer():
    """非队友 P3 跳预言家时，正常作为夜刀首选。"""
    ctx = _wolf_context(
        phase=Phase.NIGHT_WEREWOLF,
        allowed_actions=[ActionType.NIGHT_KILL_NOMINATE],
        visible=_alive("P1", "P2", "P3", "P4", claims={"P3": Role.SEER.value}),
    )
    action = WerewolfStrategy().decide(ctx)
    assert action.target == "P3"


def test_tie_revote_prefers_non_teammate_candidate():
    """平票候选含队友 P2 和非队友 P3 时，优先投 P3。"""
    ctx = _wolf_context(
        phase=Phase.DAY_TIE_REVOTE,
        allowed_actions=[ActionType.VOTE],
        visible=_alive("P1", "P2", "P3", "P4"),
        tie_candidates=["P2", "P3"],
    )
    action = WerewolfStrategy().decide(ctx)
    assert action.action_type == ActionType.VOTE
    assert action.target == "P3"
