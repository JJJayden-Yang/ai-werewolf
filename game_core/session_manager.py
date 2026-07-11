"""GameSessionManager —— Task A1。

创建 / 读取 / 保存 GameSession。每个 game_id 独立隔离，支持多局并发，
禁止使用全局单例 GameState。

create_game 的职责仅限「发牌 + 构造初始 TruthState + 建 GameSession」，
不推进状态机、不产生事件（那是 PhaseController / Supervisor 的事）。
"""

from __future__ import annotations

import random

from contracts import (
    Camp,
    GameConfig,
    HunterState,
    NightState,
    Phase,
    PlayerState,
    PlayerStatus,
    Role,
    RoundState,
    TruthState,
    WitchState,
)

from game_core.types import GameSession


class GameSessionManager:
    def __init__(self, seed: int | None = None, rng: random.Random | None = None) -> None:
        # 角色洗牌的随机源。优先用显式 rng；否则用 seed 起一个定种 Random（seed=None→非确定性）。
        # 记录 seed 以便 replay 复现发牌：传 seed=（而非 rng=）时才记得下来。
        self.seed = seed
        self._rng = rng if rng is not None else random.Random(seed)
        self._games: dict[str, GameSession] = {}

    def create_game(
        self,
        config: GameConfig,
        *,
        fixed_roles: dict[str, Role] | None = None,
    ) -> GameSession:
        if config.game_id in self._games:
            raise ValueError(f"game already exists: {config.game_id}（默认不覆盖）")
        player_ids = [f"P{i}" for i in range(1, config.player_count + 1)]
        roles_by_player = self._assign_roles(player_ids, self._build_role_deck(config), fixed_roles)
        players = {
            pid: PlayerState(
                role=role,
                camp=Camp.WEREWOLF if role == Role.WEREWOLF else Camp.VILLAGER,
                status=PlayerStatus.ALIVE,
                public_claim=None,
            )
            for pid, role in roles_by_player.items()
        }

        # 发牌完成、准备进入第一夜；后续 phase 流转由 PhaseController(A2) 负责。
        truth_state = TruthState(
            game_id=config.game_id,
            round=1,
            phase=Phase.NIGHT_WEREWOLF,
            players=players,
            witch_state=WitchState(),
            hunter_state=HunterState(),
            night_state=NightState(),
            round_state=RoundState(),
        )
        session = GameSession(
            game_id=config.game_id, config=config, truth_state=truth_state, seed=self.seed
        )
        self._games[config.game_id] = session
        return session

    def get_game(self, game_id: str) -> GameSession:
        try:
            return self._games[game_id]
        except KeyError:
            raise KeyError(f"game not found: {game_id}") from None

    def save_game(self, session: GameSession) -> None:
        self._games[session.game_id] = session

    def list_games(self) -> list[GameSession]:
        return list(self._games.values())

    def _build_role_deck(self, config: GameConfig) -> list[Role]:
        counts = config.roles
        count_by_role = {
            Role.WEREWOLF: counts.werewolf,
            Role.SEER: counts.seer,
            Role.WITCH: counts.witch,
            Role.HUNTER: counts.hunter,
            Role.VILLAGER: counts.villager,
        }
        negatives = {r.value: c for r, c in count_by_role.items() if c < 0}
        if negatives:
            raise ValueError(f"role count 不能为负: {negatives}")

        deck: list[Role] = [role for role, c in count_by_role.items() for _ in range(c)]
        if len(deck) != config.player_count:
            raise ValueError(
                f"roles 之和 {len(deck)} 与 player_count {config.player_count} 不一致"
            )
        return deck

    def _assign_roles(
        self,
        player_ids: list[str],
        deck: list[Role],
        fixed_roles: dict[str, Role] | None,
    ) -> dict[str, Role]:
        fixed_roles = fixed_roles or {}
        unknown_seats = sorted(set(fixed_roles) - set(player_ids))
        if unknown_seats:
            raise ValueError(f"fixed role seats 不存在: {unknown_seats}")

        remaining = list(deck)
        assignments: dict[str, Role] = {}
        for pid, role in fixed_roles.items():
            try:
                remaining.remove(role)
            except ValueError:
                raise ValueError(f"fixed role {role.value!r} 不在本局角色池或数量不足") from None
            assignments[pid] = role

        self._rng.shuffle(remaining)
        for pid, role in zip((pid for pid in player_ids if pid not in assignments), remaining):
            assignments[pid] = role
        return assignments
