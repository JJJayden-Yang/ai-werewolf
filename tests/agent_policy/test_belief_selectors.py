from contracts import ActionType, AgentContext, Phase, PlayerStatus, Role, VisiblePlayer

from agent_policy.belief_selectors import select_top_belief_suspect


def _context() -> AgentContext:
    return AgentContext(
        game_id="g_belief_selector",
        agent_id="P1",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_VOTE,
        visible_players=[
            VisiblePlayer(player_id="P1", status=PlayerStatus.ALIVE),
            VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE),
            VisiblePlayer(player_id="P3", status=PlayerStatus.ALIVE),
            VisiblePlayer(player_id="P4", status=PlayerStatus.DEAD),
        ],
        belief_top_suspects=[
            {"player_id": "P4", "werewolf_prob": 0.95},
            {"player_id": "P1", "werewolf_prob": 0.90},
            {"player_id": "P2", "werewolf_prob": 0.70},
            {"player_id": "P3", "werewolf_prob": 0.62},
        ],
        allowed_actions=[ActionType.VOTE],
    )


def test_select_top_belief_suspect_skips_dead_players_and_self():
    assert select_top_belief_suspect(_context(), min_werewolf_prob=0.60) == "P2"


def test_select_top_belief_suspect_respects_threshold():
    assert select_top_belief_suspect(_context(), min_werewolf_prob=0.80) is None


def test_select_top_belief_suspect_respects_candidate_scope():
    assert (
        select_top_belief_suspect(
            _context(),
            min_werewolf_prob=0.50,
            candidate_ids=["P3"],
        )
        == "P3"
    )


# --------------------------------------------------------------------------- #
# tier helper: derive_suspicion_tiers
# --------------------------------------------------------------------------- #

from contracts import BeliefState, RoleBelief  # noqa: E402

from agent_policy.belief_selectors import derive_suspicion_tiers  # noqa: E402


def _belief(probs: dict[str, float]) -> BeliefState:
    return BeliefState(
        game_id="g",
        agent_id="P1",
        round=1,
        beliefs={pid: RoleBelief(werewolf=w) for pid, w in probs.items()},
    )


def test_derive_tiers_strong_when_top1_clearly_leads():
    bs = _belief({"P2": 0.8, "P3": 0.2, "P4": 0.1})
    tiers = derive_suspicion_tiers(bs, Role.WEREWOLF, alive_set={"P2", "P3", "P4"})
    assert tiers.top1 == ("P2", 0.8)
    assert tiers.top2 == ("P3", 0.2)
    assert abs(tiers.margin - 0.6) < 1e-9
    assert tiers.tier == "strong"


def test_derive_tiers_lean_for_moderate_margin():
    bs = _belief({"P2": 0.32, "P3": 0.25, "P4": 0.2})
    tiers = derive_suspicion_tiers(bs, Role.WEREWOLF, alive_set={"P2", "P3", "P4"})
    assert tiers.top1[0] == "P2"
    assert abs(tiers.margin - 0.07) < 1e-9
    assert tiers.tier == "lean"


def test_derive_tiers_flat_when_indistinguishable():
    bs = _belief({"P2": 0.30, "P3": 0.28, "P4": 0.27})
    tiers = derive_suspicion_tiers(bs, Role.WEREWOLF, alive_set={"P2", "P3", "P4"})
    assert tiers.tier == "flat"  # margin 0.02 < lean 阈值 0.05


def test_derive_tiers_excludes_dead_and_self():
    bs = _belief({"P1": 0.9, "P2": 0.6, "P3": 0.1})
    # P1 自己 exclude，P3 已死不在 alive_set → 只剩 P2 一个候选。
    tiers = derive_suspicion_tiers(
        bs, Role.WEREWOLF, alive_set={"P1", "P2"}, exclude={"P1"}
    )
    assert tiers.top1 == ("P2", 0.6)
    assert tiers.top2 is None
    assert tiers.margin == 0.6  # 无 top2 → 减 0
    # 单候选 margin 0.6 ≥ strong 阈值
    assert tiers.tier == "strong"


def test_derive_tiers_flat_when_no_candidates():
    bs = _belief({"P1": 0.9})
    tiers = derive_suspicion_tiers(bs, Role.WEREWOLF, alive_set={"P1"}, exclude={"P1"})
    assert tiers.top1 is None
    assert tiers.tier == "flat"


def test_derive_tiers_threshold_override():
    bs = _belief({"P2": 0.8, "P3": 0.2})
    # 抬高 strong 阈值到 0.7 → margin 0.6 落 lean。
    tiers = derive_suspicion_tiers(
        bs, Role.WEREWOLF, alive_set={"P2", "P3"}, strong_margin=0.7, lean_margin=0.05
    )
    assert tiers.tier == "lean"
