from contracts import ActionType, AgentContext, Phase, Role, VisiblePlayer, PlayerStatus

from agent_policy.actions import available_actions_for_context, build_vote_action


def test_available_actions_is_role_actions_intersect_allowed_actions():
    context = AgentContext(
        game_id="g001",
        agent_id="P1",
        role=Role.WEREWOLF,
        round=1,
        phase=Phase.DAY_VOTE,
        allowed_actions=[ActionType.VOTE, ActionType.CHECK],
    )

    assert available_actions_for_context(context) == {ActionType.VOTE}


def test_build_vote_action_returns_standard_agent_action_json():
    context = AgentContext(
        game_id="g001",
        agent_id="P2",
        role=Role.VILLAGER,
        round=1,
        phase=Phase.DAY_VOTE,
        visible_players=[
            VisiblePlayer(player_id="P1", status=PlayerStatus.ALIVE),
            VisiblePlayer(player_id="P2", status=PlayerStatus.ALIVE),
        ],
        allowed_actions=[ActionType.VOTE],
    )

    action = build_vote_action(context, "P1")
    data = action.model_dump(mode="json")

    assert data["game_id"] == "g001"
    assert data["agent_id"] == "P2"
    assert data["role"] == "villager"
    assert data["phase"] == "DAY_VOTE"
    assert data["action_type"] == "vote"
    assert data["target"] == "P1"
    assert data["metadata"]["policy_module"] == "agent_policy.actions"
