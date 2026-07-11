"""Belief injection arm filters for Phase 6 mixed-belief experiments."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Literal

from contracts import Camp, Role

if TYPE_CHECKING:
    from game_core import GameEngine


ArmScope = Literal["wolves", "villagers", "gods", "civilians", "all", "none"]


def make_arm_filter(scope: ArmScope, engine: "GameEngine", game_id: str) -> Callable[[str], bool]:
    """构造一个 snapshot belief 注入过滤器。

    立即查 truth_state，冻结到 frozenset，filter 只查 frozenset。即便 caller
    后续改 truth_state，filter 行为不变。

    scope:
      - wolves: camp == Camp.WEREWOLF
      - villagers: camp == Camp.VILLAGER
      - gods: role in {Role.SEER, Role.WITCH, Role.HUNTER}
      - civilians: role == Role.VILLAGER
      - all: 全部
      - none: 无人

    注意：scope="none" 只表示 belief 不注入任何 agent，不等价于关闭 belief lane。
    如果 caller 仍以 arm="v1" 调用 build_game，belief updater 仍会维护 real belief；
    纯 v0 / 无 belief lane 必须由上层选择 arm="v0" 且不传 factory。
    """

    players = engine.sessions.get_game(game_id).truth_state.players
    god_roles = {Role.SEER, Role.WITCH, Role.HUNTER}

    if scope == "wolves":
        matched = frozenset(
            player_id
            for player_id, player in players.items()
            if player.camp == Camp.WEREWOLF
        )
    elif scope == "villagers":
        matched = frozenset(
            player_id
            for player_id, player in players.items()
            if player.camp == Camp.VILLAGER
        )
    elif scope == "gods":
        matched = frozenset(
            player_id for player_id, player in players.items() if player.role in god_roles
        )
    elif scope == "civilians":
        matched = frozenset(
            player_id for player_id, player in players.items() if player.role == Role.VILLAGER
        )
    elif scope == "all":
        matched = frozenset(players)
    elif scope == "none":
        matched = frozenset()
    else:
        raise ValueError(f"unknown arm filter scope: {scope!r}")

    return lambda agent_id: agent_id in matched


def make_arm_filter_factory(
    scope: ArmScope,
) -> Callable[["GameEngine", str], Callable[[str], bool]]:
    """make_arm_filter 的延迟版本，给 build_game.belief_inject_filter_factory 用。"""

    return lambda engine, game_id: make_arm_filter(scope, engine, game_id)
